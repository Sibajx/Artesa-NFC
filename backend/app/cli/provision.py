"""Provisioning de certificados y tags NFC (issue N-09).

    python -m app.cli.provision list
    python -m app.cli.provision status --piece CODIGO
    python -m app.cli.provision issue  --piece CODIGO [--dry-run]
    python -m app.cli.provision rotate --piece CODIGO [--dry-run]
    python -m app.cli.provision revoke --piece CODIGO [--dry-run]
    python -m app.cli.provision lock   --piece CODIGO [--dry-run]

Herramienta operativa sensible (docs/PROVISIONING.md). Los nombres de
subcomandos y los códigos siguen en inglés; todo lo que lee el operador está en
español para reducir errores humanos.

Reglas de seguridad, todas verificadas por tests:

* El token en claro NUNCA se recibe: ni por argv, stdin, variables de entorno,
  archivos ni flags. No existe ninguna opción capaz de aceptarlo.
* El token solo se muestra como parte de la URL completa
  ``https://artesanfc.com/c/{token}`` (nunca aislado, nunca su hash), UNA vez,
  después de que el commit que guarda su hash terminó bien, en la pantalla
  alterna de un terminal interactivo. Sin TTY el comando se niega antes de
  generar nada.
* No escribe archivos, no usa ``logging``, no imprime tracebacks (solo el
  nombre de la clase de una excepción inesperada) y desactiva los core dumps.
* Sin ``--dry-run`` cada paso pide confirmaciones tipeadas. Con ``--dry-run``
  todo es de solo lectura (``SET TRANSACTION READ ONLY``) y no se genera token.
* Guard de entorno antes de abrir ninguna conexión
  (``assert_safe_for_provisioning``); en ``production`` hay que teclear el
  nombre del entorno.

Códigos de salida:

    0  éxito (incluye --dry-run)
    1  error inesperado
    2  rechazado por la política de seguridad (entorno, base de datos, sin TTY)
    3  precondición fallida o entrada inválida; no se cambió nada
    4  cancelado por el operador; no queda nada pendiente
    5  error de base de datos o concurrencia; este paso no se confirmó
    6  ESTADO PARCIAL ya confirmado en la base de datos: requiere seguimiento
       (el mensaje dice el comando exacto)
"""
from __future__ import annotations

import argparse
import getpass
import os
import re
import sys
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.db_safety import (
    ENV_PRODUCTION,
    UnsafeConfigurationError,
    assert_safe_for_provisioning,
    is_local_database_target,
    parse_database_target,
)

EXIT_OK = 0
EXIT_UNEXPECTED = 1
EXIT_POLICY = 2
EXIT_PRECONDITION = 3
EXIT_ABORTED = 4
EXIT_DATABASE = 5
EXIT_PARTIAL = 6

_MAX_TRIES = 3
_UID_TAIL_SEPARATORS_RE = re.compile(r"[:\-\s]")

# Pantalla alterna: el terminal no la guarda en el scrollback, así que al
# salir de ella la URL desaparece. Mejor esfuerzo: no protege contra una
# captura/grabación de pantalla ni contra un emulador que registre la salida.
_ALT_SCREEN_ON = "\x1b[?1049h\x1b[2J\x1b[H"
_ALT_SCREEN_OFF = "\x1b[2J\x1b[3J\x1b[H\x1b[?1049l"

_BLOCKER_MESSAGES = {
    "piece_not_found": "No existe ninguna pieza con ese código público.",
    "piece_not_published": (
        "La pieza NO está publicada. Un certificado de una pieza no publicada se "
        "muestra como no disponible, así que su tag no se podría verificar. "
        "Publíquela primero."
    ),
    "artisan_not_published": (
        "El artesano de la pieza NO está publicado (mismo motivo). Publíquelo primero."
    ),
    "active_certificate_exists": (
        "La pieza ya tiene un certificado ACTIVO. Para cambiarlo use 'rotate'; "
        "para anularlo, 'revoke'."
    ),
    "no_active_certificate": "La pieza no tiene un certificado activo.",
    "tag_in_use": (
        "La pieza ya tiene un tag NFC vigente (disponible, programado o bloqueado). "
        "Revíselo con 'status'."
    ),
    "same_tag_unavailable": (
        "No hay exactamente un tag vigente que se pueda reescribir; use un tag NUEVO."
    ),
    "tag_locked_requires_new_tag": (
        "El tag está BLOQUEADO y no se puede reescribir; hay que usar un tag NUEVO."
    ),
    "tag_already_locked": "El tag de la pieza ya está registrado como bloqueado.",
    "no_programmed_tag": (
        "La pieza no tiene exactamente un tag en estado 'programado' que se pueda bloquear."
    ),
    "tag_not_available": "El tag ya no está en estado 'disponible'; el estado cambió.",
    "invalid_reason": "Motivo inválido.",
}

_UID_ERROR_MESSAGES = {
    "empty": "El UID está vacío.",
    "too_long": "Ese texto es demasiado largo para ser un UID.",
    "not_hex": (
        "Un UID solo tiene dígitos hexadecimales (0-9, A-F), con o sin separadores "
        "':' '-' o espacios."
    ),
    "wrong_length": "El UID de un NTAG213 tiene 7 bytes (14 dígitos hexadecimales).",
    "not_nxp": "El UID de un NTAG213 empieza con 04.",
}

_RECOMMENDATIONS = {
    "issue": "Siguiente paso: 'issue' (primera emisión de esta pieza).",
    "revoked_with_tags": (
        "Estado inconsistente: la pieza conserva tags vigentes sin certificado activo. "
        "Requiere revisión manual (docs/PROVISIONING.md)."
    ),
    "verify_then_optional_lock": (
        "Tag programado. Verifique con varios escaneos. El bloqueo físico es opcional y "
        "va aparte ('lock', solo si su herramienta NFC lo confirma). Para cambiar la URL "
        "use 'rotate'; para anular el certificado, 'revoke'."
    ),
    "locked": (
        "Tag bloqueado. Para cambiar la URL hay que usar un tag nuevo ('rotate'); "
        "para anular el certificado, 'revoke'."
    ),
    "interrupted_rotate": (
        "Emisión interrumpida: hay un certificado activo cuyo tag no está programado y "
        "su token no se puede recuperar. Use 'rotate' para emitir uno nuevo y volver "
        "a escribir el tag."
    ),
}


class CliExit(Exception):
    """Fin controlado de un comando con un código de salida y un mensaje."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class OperatorAbort(Exception):
    """El operador cerró la entrada (EOF) o pulsó Ctrl-C en un prompt."""


# --- Terminal -----------------------------------------------------------------


class ConsoleTerminal:
    """Entrada/salida real. Tests inyectan otro terminal; `reveal` es el ÚNICO
    canal por el que puede salir la URL con el token."""

    def say(self, text_: str = "") -> None:
        sys.stdout.write(text_ + "\n")
        sys.stdout.flush()

    def warn(self, text_: str) -> None:
        sys.stderr.write(text_ + "\n")
        sys.stderr.flush()

    def ask(self, prompt: str) -> str:
        try:
            return input(prompt)
        except (EOFError, KeyboardInterrupt):
            sys.stdout.write("\n")
            raise OperatorAbort from None

    def secret_channel_ok(self) -> bool:
        """La URL con el token solo se muestra en un terminal interactivo real
        (stdin, stdout y stderr son TTY) que entienda la pantalla alterna."""
        streams_ok = all(s.isatty() for s in (sys.stdin, sys.stdout, sys.stderr))
        return streams_ok and os.environ.get("TERM", "") not in ("", "dumb")

    def reveal(self, lines: Sequence[str], hide_prompt: str) -> None:
        sys.stdout.write(_ALT_SCREEN_ON)
        for line in lines:
            sys.stdout.write(line + "\n")
        sys.stdout.flush()
        try:
            self.ask(hide_prompt)
        finally:
            sys.stdout.write(_ALT_SCREEN_OFF)
            sys.stdout.flush()


# --- Parser -------------------------------------------------------------------


class _Parser(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, add_help=False, **kwargs)
        self.add_argument("-h", "--help", action="help", help="muestra esta ayuda y termina")

    def error(self, message: str) -> None:  # type: ignore[override]
        # argparse's own text is English (and could echo a bad value): use a
        # fixed Spanish message instead.
        raise CliExit(
            EXIT_PRECONDITION,
            "Uso incorrecto: falta un argumento obligatorio o hay una opción/comando no válido.\n\n"
            f"{self.format_usage()}",
        )


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="python -m app.cli.provision",
        description=(
            "Provisioning de certificados y tags NFC de ArtesaNFC (issue N-09). "
            "Ninguna opción acepta un token: el token solo se muestra, una vez, "
            "como parte de la URL."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMANDO", parser_class=_Parser)

    sub.add_parser("list", help="lista las piezas y el estado de su certificado y tag (solo lectura)")

    def piece_command(name: str, help_: str, *, dry_run: bool) -> None:
        cmd = sub.add_parser(name, help=help_)
        cmd.add_argument("--piece", required=True, metavar="CODIGO", help="public_code de la pieza")
        if dry_run:
            cmd.add_argument(
                "--dry-run",
                action="store_true",
                help="simulacro de solo lectura: muestra el plan, no genera token ni cambia nada",
            )

    piece_command("status", "estado de una pieza y siguiente paso recomendado (solo lectura)", dry_run=False)
    piece_command("issue", "primera emisión: registra el tag, emite el certificado y guía la escritura", dry_run=True)
    piece_command("rotate", "revoca el certificado activo y emite uno nuevo (nueva escritura)", dry_run=True)
    piece_command("revoke", "revoca el certificado activo y retira el tag", dry_run=True)
    piece_command("lock", "registra en la base de datos el bloqueo físico ya realizado del tag", dry_run=True)
    return parser


# --- Helpers ------------------------------------------------------------------


def _operator() -> str:
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001 - only a provenance label
        return "unknown"


def _disable_core_dumps() -> None:
    """El token vive en la memoria del proceso: sin core dumps."""
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except (ImportError, ValueError, OSError):
        pass


def _uid_tail(uid: str) -> str:
    return uid[-5:]  # "E5:F6"


def _normalize_tail(raw: str) -> str:
    return _UID_TAIL_SEPARATORS_RE.sub("", raw).upper()


# --- Provisioner --------------------------------------------------------------


class Provisioner:
    def __init__(
        self,
        terminal,
        session_factory: Callable[[], object],
        app_env: str,
        database_url: str,
        operator: str,
    ) -> None:
        # Importar aquí (después del guard de `run`): estos módulos cargan la
        # configuración y el engine al importarse.
        from app.services import certificates, lifecycle, nfc_tags, provisioning

        self.term = terminal
        self.session_factory = session_factory
        self.app_env = app_env
        self.database_url = database_url
        self.operator = operator
        self.prov = provisioning
        self.certs = certificates
        self.tags = nfc_tags
        self.lifecycle = lifecycle
        # Texto de seguimiento mientras haya estado confirmado sin terminar.
        self.partial: str | None = None

    # -- sesiones ------------------------------------------------------------

    @contextmanager
    def _read_only(self) -> Iterator[object]:
        """Solo lectura garantizada por PostgreSQL, no por disciplina."""
        with self.session_factory() as session:
            try:
                session.execute(text("SET TRANSACTION READ ONLY"))
                yield session
            finally:
                session.rollback()

    def _read(self, step):
        try:
            with self._read_only() as session:
                return step(session)
        except CliExit:
            raise
        except Exception as exc:  # noqa: BLE001
            raise self._translate(exc) from None

    def _write(self, step):
        """Un paso = una transacción: commit si todo sale bien, rollback si no."""
        try:
            with self.session_factory() as session:
                try:
                    result = step(session)
                    session.commit()
                except BaseException:
                    session.rollback()
                    raise
            return result
        except CliExit:
            raise
        except Exception as exc:  # noqa: BLE001
            raise self._translate(exc) from None

    def _translate(self, exc: Exception) -> CliExit:
        """Nunca se imprime `str(exc)` de la base de datos ni un traceback."""
        if isinstance(exc, self.prov.PreconditionFailed):
            lines = "\n".join(f"  - {_BLOCKER_MESSAGES.get(c, c)}" for c in exc.codes)
            return CliExit(EXIT_PRECONDITION, f"No se puede continuar:\n{lines}\nNo se modificó nada en este paso.")
        if isinstance(exc, self.tags.InvalidPhysicalUid):
            return CliExit(EXIT_PRECONDITION, _UID_ERROR_MESSAGES.get(exc.reason, "UID inválido."))
        if isinstance(exc, self.tags.NfcTagUidAlreadyRegistered):
            return CliExit(
                EXIT_PRECONDITION,
                "Ese UID ya está registrado (los tags nunca se borran). Use otro tag físico. "
                "No se modificó nada en este paso.",
            )
        if isinstance(exc, self.lifecycle.LifecycleConflict):
            return CliExit(
                EXIT_DATABASE,
                "Conflicto de concurrencia: otro proceso cambió esta pieza. Revise 'status' y "
                "decida de nuevo. Este paso no se confirmó.",
            )
        if isinstance(exc, self.lifecycle.LifecycleIntegrityError):
            constraint = f", restricción {exc.constraint}" if getattr(exc, "constraint", None) else ""
            return CliExit(
                EXIT_DATABASE,
                f"Fallo de integridad de la base de datos ({type(exc).__name__}{constraint}). "
                "Este paso no se confirmó.",
            )
        if isinstance(exc, self.lifecycle.LifecycleError):
            return CliExit(
                EXIT_PRECONDITION,
                f"Operación rechazada por el estado actual de los datos ({type(exc).__name__}). "
                "No se modificó nada en este paso.",
            )
        if isinstance(exc, SQLAlchemyError):
            return CliExit(
                EXIT_DATABASE,
                f"Error de base de datos ({type(exc).__name__}). Este paso no se confirmó.",
            )
        return CliExit(
            EXIT_UNEXPECTED,
            f"Error inesperado ({type(exc).__name__}). Detalle omitido a propósito: "
            "podría contener datos sensibles.",
        )

    # -- preguntas ---------------------------------------------------------------

    def _yes_no(self, prompt: str, *, default: bool = False) -> bool:
        answer = self.term.ask(prompt).strip().lower()
        if not answer:
            return default
        return answer in ("s", "si", "sí")

    def _confirm_typed(self, expected: str, prompt: str) -> None:
        if self.term.ask(prompt).strip() != expected:
            raise CliExit(EXIT_ABORTED, "Confirmación incorrecta. Operación cancelada; no se modificó nada.")

    def _ask_reason(self) -> str:
        options = ", ".join(self.prov.REVOCATION_REASONS)
        for _ in range(_MAX_TRIES):
            reason = self.term.ask(f"Motivo ({options}): ").strip()
            if reason in self.prov.REVOCATION_REASONS:
                return reason
            self.term.warn("Motivo no reconocido: escriba exactamente uno de la lista.")
        raise CliExit(EXIT_PRECONDITION, "No se indicó un motivo válido. No se modificó nada.")

    def _ask_uid(self, purpose: str) -> str:
        for _ in range(_MAX_TRIES):
            raw = self.term.ask(
                f"UID del tag NFC {purpose} (7 bytes, p. ej. 04:A1:B2:C3:D4:E5:F6; léalo con su herramienta NFC): "
            )
            try:
                uid = self.tags.normalize_physical_uid(raw)
            except self.tags.InvalidPhysicalUid as exc:
                self.term.warn(_UID_ERROR_MESSAGES.get(exc.reason, "UID inválido."))
                continue
            self.term.say(f"Interpretado como {uid} (NTAG213, 13.56 MHz, ISO 14443A).")
            if not self._yes_no("¿Es correcto? [s/N]: "):
                continue
            if self._read(lambda db: self.prov.physical_uid_registered(db, uid)):
                raise CliExit(
                    EXIT_PRECONDITION,
                    "Ese UID ya está registrado (los tags nunca se borran). Use otro tag físico. "
                    "No se modificó nada.",
                )
            return uid
        raise CliExit(EXIT_PRECONDITION, "No se obtuvo un UID válido y confirmado. No se modificó nada.")

    # -- pantalla ----------------------------------------------------------------

    def _banner(self) -> None:
        database = parse_database_target(self.database_url).database or "(sin nombre)"
        host = "local" if is_local_database_target(self.database_url) else "REMOTO"
        self.term.say("ArtesaNFC - provisioning de certificados y tags NFC")
        self.term.say(f"Entorno: APP_ENV={self.app_env}   base de datos: {database} (host: {host})")
        if self.app_env != ENV_PRODUCTION:
            self.term.say(
                f"*** ENSAYO LOCAL: la URL usará {self.prov.REHEARSAL_URL_BASE} "
                "y NO sirve para un tag real. No la escriba en un tag de una pieza real. ***"
            )
        self.term.say()

    def _start_write_command(self, *, secret: bool, dry_run: bool) -> None:
        """Antes de cualquier consulta: exige TTY si va a mostrar un secreto,
        y (en producción) que el operador teclee el nombre del entorno."""
        if dry_run:
            self._banner()
            return
        if secret and not self.term.secret_channel_ok():
            raise CliExit(
                EXIT_POLICY,
                "Este comando muestra una URL secreta y exige un terminal interactivo real "
                "(stdin, stdout y stderr conectados a un TTY, TERM válido; sin redirecciones ni "
                "tuberías). No se generó ningún token ni se tocó la base de datos.",
            )
        _disable_core_dumps()
        self._banner()
        if self.app_env == ENV_PRODUCTION:
            self._confirm_typed(
                ENV_PRODUCTION,
                "Está operando sobre PRODUCCIÓN. Escriba 'production' para continuar: ",
            )
            self.term.say()

    def _describe(self, state) -> None:
        say = self.term.say
        say(f"Pieza      : {state.public_code} - {state.name} (slug {state.slug})")
        say(f"Artesano   : {state.artisan_name}")
        say(
            "Publicación: pieza="
            + ("publicada" if state.piece_published else "NO PUBLICADA")
            + "  artesano="
            + ("publicado" if state.artisan_published else "NO PUBLICADO")
        )
        if state.active_certificate is not None:
            since = state.active_certificate.issued_at
            say(f"Certificado: ACTIVO desde {since.isoformat() if since else '?'} ({state.revoked_certificates} revocados en el historial)")
        else:
            say(f"Certificado: ninguno activo ({state.revoked_certificates} revocados en el historial)")
        if state.tags:
            for tag in state.tags:
                say(f"Tag NFC    : {tag.physical_uid or '(sin UID)'} [{tag.status.value}]")
        else:
            say("Tag NFC    : ninguno vigente")
        say()

    def _load_state(self, public_code: str):
        state = self._read(lambda db: self.prov.load_piece_state(db, public_code))
        if state is None:
            raise CliExit(EXIT_PRECONDITION, _BLOCKER_MESSAGES["piece_not_found"])
        return state

    def _require_eligible(self, blockers: list[str]) -> None:
        if blockers:
            lines = "\n".join(f"  - {_BLOCKER_MESSAGES.get(c, c)}" for c in blockers)
            raise CliExit(EXIT_PRECONDITION, f"No se puede continuar:\n{lines}\nNo se modificó nada.")

    def _summary(self, *records: str) -> None:
        self.term.say("Registro de la operación (sin secretos; consérvelo en su bitácora):")
        for record in records:
            self.term.say(f"  {record}")

    # -- comandos de solo lectura ---------------------------------------------------

    def cmd_list(self, args) -> int:
        self._banner()

        def load(db):
            return [self.prov.load_piece_state(db, code) for code in self.prov.list_public_codes(db)]

        states = self._read(load)
        if not states:
            self.term.say("No hay piezas.")
            return EXIT_OK
        for state in states:
            cert = "cert=ACTIVO" if state.active_certificate else "cert=ninguno"
            tags = ",".join(f"{t.physical_uid or '?'}[{t.status.value}]" for t in state.tags) or "ninguno"
            published = "publicada" if state.piece_published and state.artisan_published else "NO publicable"
            self.term.say(f"{state.public_code} | {state.name} | {state.artisan_name} | {published} | {cert} | tag={tags}")
        return EXIT_OK

    def cmd_status(self, args) -> int:
        self._banner()
        state = self._load_state(args.piece)
        self._describe(state)
        self.term.say(_RECOMMENDATIONS[self.prov.recommended_action(state)])
        return EXIT_OK

    # -- issue ---------------------------------------------------------------------

    def cmd_issue(self, args) -> int:
        self._start_write_command(secret=not args.dry_run, dry_run=args.dry_run)
        state = self._load_state(args.piece)
        self._describe(state)
        blockers = self.prov.issue_blockers(state)
        self._require_eligible(blockers)

        if args.dry_run:
            self._dry_run_plan(
                "issue",
                [
                    "Pedirá el UID del tag NFC (NTAG213) y confirmará la pieza tecleando su código.",
                    "En UNA sola transacción: registrar el tag, asignarlo a la pieza y emitir un "
                    "certificado nuevo (token aleatorio de 256 bits; solo se guarda su hash SHA-256).",
                    "Autochequeo en proceso y, después del commit, mostrar la URL UNA sola vez.",
                    "Tras escribir y leer de vuelta el tag, registrarlo como programado.",
                    "Verificación final con un escaneo de teléfono. El bloqueo físico es un paso aparte.",
                ],
            )
            return EXIT_OK

        self._confirm_typed(state.public_code, "Para confirmar que es la pieza que tiene en las manos, reescriba su código: ")
        uid = self._ask_uid("que va a escribir")
        self.term.say()
        self.term.say("Se confirmará en UNA sola transacción:")
        self.term.say(f"  - registrar el tag {uid} y asignarlo a {state.public_code}")
        self.term.say(f"  - emitir un certificado nuevo para {state.public_code} (solo se guarda el hash)")
        self.term.say("La URL se mostrará UNA sola vez, después del commit, solo en este terminal.")
        self._confirm_typed(f"EMITIR {state.public_code}", f"Escriba 'EMITIR {state.public_code}' para continuar: ")

        result = self._write(lambda db: self.prov.execute_issue(db, state.public_code, uid, operator=self.operator))
        self.partial = self._partial_after_commit(state.public_code)
        self.term.say("Confirmado en la base de datos.")
        return self._write_and_verify(
            state=state,
            tag_id=result.tag_id,
            tag_uid=result.physical_uid,
            raw_token=result.raw_token,
            needs_program=True,
            records=[result.record],
        )

    # -- rotate --------------------------------------------------------------------

    def cmd_rotate(self, args) -> int:
        self._start_write_command(secret=not args.dry_run, dry_run=args.dry_run)
        state = self._load_state(args.piece)
        self._describe(state)
        self._require_eligible(self.prov.rotate_blockers(state))

        same_tag_ok = (
            len(state.tags) == 1 and state.tags[0].status.value in ("available", "programmed")
        )

        if args.dry_run:
            self._dry_run_plan(
                "rotate",
                [
                    "Pedirá el motivo (código) y decidirá si reescribe el MISMO tag o usa uno NUEVO"
                    + (" (aquí: solo tag nuevo)." if not same_tag_ok else "."),
                    "REVOCA el certificado activo (irreversible: la URL actual deja de autenticar "
                    "de inmediato) y emite uno nuevo, todo en una transacción.",
                    "Tag nuevo: retira los tags vigentes de la pieza, registra el nuevo y lo asigna.",
                    "Autochequeo, URL mostrada una sola vez, escritura, lectura de vuelta y escaneo.",
                ],
            )
            return EXIT_OK

        self._confirm_typed(state.public_code, "Para confirmar que es la pieza que tiene en las manos, reescriba su código: ")
        reason = self._ask_reason()

        new_uid: str | None = None
        if same_tag_ok:
            current = state.tags[0]
            choice = self.term.ask(
                f"Tag actual {current.physical_uid or '(sin UID)'} [{current.status.value}]. "
                "[m] reescribir el MISMO tag / [n] usar un tag NUEVO: "
            ).strip().lower()
            if choice == "n":
                new_uid = self._ask_uid("NUEVO")
            elif choice != "m":
                raise CliExit(EXIT_ABORTED, "Opción no reconocida. Operación cancelada; no se modificó nada.")
        else:
            if state.locked_tags:
                self.term.say("El tag actual está BLOQUEADO: no se puede reescribir. Hay que usar un tag NUEVO.")
            else:
                self.term.say("No hay un único tag que reescribir: se usará un tag NUEVO.")
            new_uid = self._ask_uid("NUEVO")

        self.term.say()
        self.term.warn(
            "ATENCIÓN: rotar REVOCA el certificado actual. La URL que hoy está en el tag dejará "
            "de autenticar de inmediato y no se puede deshacer."
        )
        self._confirm_typed(f"ROTAR {state.public_code}", f"Escriba 'ROTAR {state.public_code}' para continuar: ")

        result = self._write(
            lambda db: self.prov.execute_rotate(
                db, state.public_code, reason=reason, new_physical_uid=new_uid, operator=self.operator
            )
        )
        self.partial = self._partial_after_commit(state.public_code)
        self.term.say("Confirmado en la base de datos (certificado anterior revocado, nuevo emitido).")
        return self._write_and_verify(
            state=state,
            tag_id=result.tag_id,
            tag_uid=result.physical_uid,
            raw_token=result.raw_token,
            needs_program=result.needs_program,
            records=[result.record],
        )

    # -- revoke --------------------------------------------------------------------

    def cmd_revoke(self, args) -> int:
        self._start_write_command(secret=False, dry_run=args.dry_run)
        state = self._load_state(args.piece)
        self._describe(state)
        self._require_eligible(self.prov.revoke_blockers(state))

        if args.dry_run:
            self._dry_run_plan(
                "revoke",
                [
                    "Pedirá el motivo (código) y una confirmación tipeada.",
                    "REVOCA el certificado activo (irreversible: la URL del tag deja de autenticar "
                    "de inmediato) y retira los tags vigentes de la pieza.",
                    "Después, un escaneo del tag debe mostrar 'no disponible'.",
                ],
            )
            return EXIT_OK

        self._confirm_typed(state.public_code, "Para confirmar que es la pieza correcta, reescriba su código: ")
        reason = self._ask_reason()
        self.term.warn(
            "ATENCIÓN: revocar es IRREVERSIBLE. La URL del tag dejará de autenticar de inmediato "
            "y los tags vigentes de la pieza se retirarán. Para volver a certificarla tendrá que "
            "usar 'issue' con un tag nuevo."
        )
        self._confirm_typed(f"REVOCAR {state.public_code}", f"Escriba 'REVOCAR {state.public_code}' para continuar: ")

        result = self._write(
            lambda db: self.prov.execute_revoke(db, state.public_code, reason=reason, operator=self.operator)
        )
        self.term.say("Certificado revocado y tags retirados. El historial se conserva.")
        self.term.say("Verifique: al escanear el tag, la página debe mostrar 'no disponible'.")
        self._summary(result.record)
        return EXIT_OK

    # -- lock ----------------------------------------------------------------------

    def cmd_lock(self, args) -> int:
        self._start_write_command(secret=False, dry_run=args.dry_run)
        state = self._load_state(args.piece)
        self._describe(state)
        self._require_eligible(self.prov.lock_blockers(state))

        if args.dry_run:
            self._dry_run_plan(
                "lock",
                [
                    "Solo registra en la base de datos un bloqueo físico que usted YA hizo con su "
                    "herramienta NFC; no bloquea nada por sí mismo.",
                    "Pedirá tres confirmaciones (verificación, bloqueo confirmado por la herramienta, "
                    "escaneo posterior), los dos últimos bytes del UID y una confirmación tipeada.",
                ],
            )
            return EXIT_OK

        tag = state.programmed_tags[0]
        self.term.warn(
            "ATENCIÓN: este comando NO bloquea el tag; registra un bloqueo físico ya hecho. "
            "El bloqueo del NTAG213 es IRREVERSIBLE. Hágalo solo si su herramienta NFC soporta "
            "el bloqueo de forma explícita y lo confirmó."
        )
        self._confirm_typed(state.public_code, "Para confirmar que es la pieza correcta, reescriba su código: ")
        if not self._yes_no("¿Verificó la escritura y hubo varios escaneos exitosos? [s/N]: "):
            raise CliExit(EXIT_ABORTED, "Cancelado: verifique antes de bloquear. No se modificó nada.")
        if not self._yes_no("¿Bloqueó físicamente el tag y su herramienta confirmó la operación? [s/N]: "):
            raise CliExit(EXIT_ABORTED, "Cancelado: primero el bloqueo físico confirmado. No se modificó nada.")
        if not self._yes_no("¿Escaneó DESPUÉS de bloquear y sigue autenticando? [s/N]: "):
            raise CliExit(EXIT_ABORTED, "Cancelado: verifique el escaneo posterior al bloqueo. No se modificó nada.")
        if tag.physical_uid:
            self._check_uid_tail(tag.physical_uid, "que muestra su herramienta para el tag bloqueado")
        self._confirm_typed(f"BLOQUEAR {state.public_code}", f"Escriba 'BLOQUEAR {state.public_code}' para continuar: ")

        result = self._write(lambda db: self.prov.execute_lock(db, state.public_code, operator=self.operator))
        self.term.say("Bloqueo registrado en la base de datos.")
        self._summary(result.record)
        return EXIT_OK

    # -- escritura y verificación (issue / rotate) ---------------------------------------

    def _partial_after_commit(self, code: str) -> str:
        return (
            f"El certificado de {code} quedó ACTIVO en la base de datos, pero NO se confirmó que su "
            "URL esté escrita en el tag; el token no se puede recuperar. Ejecute:\n"
            f"    python -m app.cli.provision rotate --piece {code}\n"
            "para emitir uno nuevo, o bien:\n"
            f"    python -m app.cli.provision revoke --piece {code}"
        )

    def _check_uid_tail(self, uid: str, context: str) -> None:
        expected = _normalize_tail(_uid_tail(uid))
        for _ in range(_MAX_TRIES):
            typed = self.term.ask(f"Escriba los ÚLTIMOS 2 BYTES del UID {context} (p. ej. E5:F6): ")
            if _normalize_tail(typed) == expected:
                return
            self.term.warn("No coincide con el tag registrado. ¿Está usando OTRO tag físico? Revíselo.")
        raise CliExit(
            EXIT_PRECONDITION,
            "El UID que muestra la herramienta no coincide con el registrado. Podría ser otro tag; "
            "no se avanzó.",
        )

    def _reveal(self, url: str, uid: str | None) -> None:
        target = f"al tag {uid}" if uid else "al tag"
        lines = [
            f"ESCRIBA ESTA URL {target},",
            "como UN SOLO registro NDEF de tipo URI y nada más:",
            "",
            f"  {url}",
            "",
            "Se muestra solo aquí. No la copie a chats, notas, tickets ni capturas, y no la",
            "deje en el portapapeles ni en el historial del teléfono al terminar.",
            "",
        ]
        self.term.reveal(lines, "Presione Enter para ocultarla...")

    def _write_and_verify(
        self,
        *,
        state,
        tag_id,
        tag_uid: str | None,
        raw_token: str,
        needs_program: bool,
        records: list[str],
    ) -> int:
        code = state.public_code
        url = self.prov.build_certificate_url(self.app_env, raw_token)

        # Autochequeo por el mismo camino de código de resolve, en proceso.
        ok = self._read(lambda db: self.prov.verify_token_resolves(db, raw_token, code))
        if not ok:
            raise CliExit(
                EXIT_DATABASE,
                "El autochequeo falló: el certificado recién emitido no se resuelve como auténtico "
                "para esta pieza. La URL NO se mostró.",
            )
        self.term.say("Autochequeo en proceso: el certificado resuelve como auténtico para esta pieza.")
        self.term.ask("Presione Enter para mostrar la URL (nadie mirando, sin grabar la pantalla)...")
        self._reveal(url, tag_uid)

        while True:
            choice = self.term.ask("[e] ya la escribí / [m] mostrar de nuevo / [a] abortar: ").strip().lower()
            if choice == "m":
                self._reveal(url, tag_uid)
            elif choice == "a":
                self._abort_after_commit(code)
            elif choice == "e":
                if self._yes_no("¿La herramienta NFC LEYÓ DE VUELTA el tag y la URL coincide con la mostrada? [s/N]: "):
                    break
                self.term.warn("Entonces la escritura no está confirmada. Corrija y vuelva a intentarlo.")
            else:
                self.term.warn("Opción no reconocida.")

        if tag_uid:
            self._check_uid_tail(tag_uid, "que muestra su herramienta tras escribir")
        else:
            self.term.warn("El tag no tiene UID registrado: no se puede comprobar que sea el tag correcto.")

        if needs_program:
            step = self._write(lambda db: self.prov.execute_program(db, code, tag_id, operator=self.operator))
        else:
            step = self._write(lambda db: self.prov.execute_rewrite_note(db, code, tag_id, operator=self.operator))
        records.append(step.record)
        self.partial = (
            f"El tag de {code} quedó registrado como programado, pero el escaneo con teléfono NO se "
            "confirmó. Escanéelo; si no muestra la pieza correcta como auténtica, ejecute:\n"
            f"    python -m app.cli.provision rotate --piece {code}"
        )
        self.term.say("Registrado como programado.")
        self.term.say()

        self.term.say("Escanee el tag con un teléfono y abra el enlace. La página debe mostrar AUTÉNTICO para")
        self.term.say(f'"{state.name}" de "{state.artisan_name}".')
        if not self._yes_no("¿Fue exactamente eso lo que se mostró? [s/N]: "):
            raise CliExit(
                EXIT_PARTIAL,
                "La verificación con teléfono NO se confirmó. No use este tag: ejecute 'rotate' "
                "para emitir una URL nueva (y, si escribió la URL de otra pieza en este tag, revoque "
                "esa pieza también).",
            )
        self.partial = None

        self.term.say()
        self._summary(*records)
        self.term.say()
        self.term.say("Siguientes pasos: pegue el tag a la pieza, escanéelo ya pegado y repita el escaneo.")
        self.term.say("El bloqueo físico es opcional y va aparte ('lock'), solo si su herramienta lo confirma.")
        self.term.say("Limpie el portapapeles y borre la URL del historial del navegador del teléfono.")
        return EXIT_OK

    def _abort_after_commit(self, code: str) -> None:
        self.term.say("Abortando: el certificado ya está confirmado en la base de datos pero sin desplegar.")
        if self._yes_no("¿Revocarlo ahora y retirar el tag? [S/n]: ", default=True):
            self._write(lambda db: self.prov.execute_revoke(db, code, reason="not-deployed", operator=self.operator))
            self.partial = None
            raise CliExit(
                EXIT_ABORTED,
                "Abortado. Certificado revocado y tag retirado; el historial se conserva.",
            )
        raise CliExit(EXIT_ABORTED, "Abortado. El certificado sigue activo sin desplegar.")

    # -- utilidades -------------------------------------------------------------------

    def _dry_run_plan(self, command: str, steps: list[str]) -> None:
        say = self.term.say
        say("SIMULACRO (--dry-run): solo lectura. No se generó ningún token ni se modificó nada.")
        say(f"Plan de '{command}':")
        for number, step in enumerate(steps, start=1):
            say(f"  {number}. {step}")
        if command in ("issue", "rotate"):
            say(
                f"Forma de la URL: {self.prov.certificate_url_base(self.app_env)}"
                f"<{self.certs.TOKEN_LENGTH} caracteres, se generan al emitir>"
            )
        say("Elegibilidad: la pieza cumple las condiciones para este comando.")

    def execute(self, args) -> int:
        handlers = {
            "list": self.cmd_list,
            "status": self.cmd_status,
            "issue": self.cmd_issue,
            "rotate": self.cmd_rotate,
            "revoke": self.cmd_revoke,
            "lock": self.cmd_lock,
        }
        code = EXIT_UNEXPECTED
        try:
            code = handlers[args.command](args)
        except CliExit as exc:
            self.term.warn(exc.message)
            code = exc.code
        except OperatorAbort:
            self.term.warn("Entrada cancelada por el operador.")
            code = EXIT_ABORTED
        except KeyboardInterrupt:
            self.term.warn("Interrumpido por el operador.")
            code = EXIT_ABORTED
        except Exception as exc:  # noqa: BLE001 - never a traceback: it could carry secrets
            self.term.warn(f"Error inesperado ({type(exc).__name__}). Detalle omitido a propósito.")
            code = EXIT_UNEXPECTED

        if self.partial is not None and code != EXIT_OK:
            self.term.warn("")
            self.term.warn("ESTADO PARCIAL: hay cambios ya confirmados que requieren seguimiento.")
            self.term.warn(self.partial)
            return EXIT_PARTIAL
        return code


# --- Entry point -----------------------------------------------------------------


def _load_environment() -> tuple[str, str]:
    from app.core.config import get_settings

    settings = get_settings()
    return settings.app_env, settings.database_url


def run(
    argv: Sequence[str] | None = None,
    *,
    terminal=None,
    session_factory: Callable[[], object] | None = None,
    environment: tuple[str, str] | None = None,
) -> int:
    """Devuelve el código de salida. `terminal`, `session_factory` y
    `environment` (app_env, database_url) se inyectan solo en tests."""
    term = terminal if terminal is not None else ConsoleTerminal()
    try:
        args = build_parser().parse_args(list(argv) if argv is not None else None)
    except CliExit as exc:
        term.warn(exc.message)
        return exc.code
    except SystemExit as exc:  # --help
        return exc.code if isinstance(exc.code, int) else EXIT_OK

    # El guard va ANTES de importar la capa de datos y de abrir ninguna conexión.
    try:
        app_env, database_url = environment if environment is not None else _load_environment()
        assert_safe_for_provisioning(app_env, database_url)
    except UnsafeConfigurationError as exc:
        term.warn(f"Configuración rechazada: {exc}")
        return EXIT_POLICY
    except Exception as exc:  # noqa: BLE001 - settings errors: type only
        term.warn(f"No se pudo cargar una configuración segura ({type(exc).__name__}).")
        return EXIT_POLICY

    if session_factory is None:
        from app.db.base import SessionLocal

        session_factory = SessionLocal

    provisioner = Provisioner(term, session_factory, app_env, database_url, _operator())
    return provisioner.execute(args)


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
