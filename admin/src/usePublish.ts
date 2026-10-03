import { ApiError, adminApi } from './api';
import { writeErrorMessage } from './format';
import { useConfirm, useToast } from './feedback-context';

type Kind = 'artisans' | 'pieces';

export interface PublishTarget {
  kind: Kind;
  id: string;
  version: string;
  name: string;
}

// Publish with the styled confirmation and a toast. With `pieces`, an
// artisan and its draft pieces are published in one go (artisan first: a
// piece is only visible when its artisan is published).
export function usePublish() {
  const confirm = useConfirm();
  const toast = useToast();

  return async function publish(target: PublishTarget, pieces: PublishTarget[] = [], artisanAlreadyPublished = false): Promise<boolean> {
    const withPieces = pieces.length > 0;
    const answer = await confirm({
      title: withPieces
        ? `¿Publicar a ${target.name} y ${pieces.length === 1 ? 'su pieza' : `sus ${pieces.length} piezas`}?`
        : `¿Publicar «${target.name}»?`,
      body: target.kind === 'pieces' && !withPieces
        ? 'Se verá en el sitio público. Una pieza solo aparece si su artesano también está publicado.'
        : 'Se verá en el sitio público de inmediato. Puedes pasarlo a borrador cuando quieras.',
      confirmLabel: 'Publicar',
    });
    if (answer === null) return false;
    try {
      if (!artisanAlreadyPublished) {
        await (target.kind === 'artisans'
          ? adminApi.transitionArtisan(target.id, target.version, 'publish')
          : adminApi.transitionPiece(target.id, target.version, 'publish'));
      }
      for (const piece of pieces) await adminApi.transitionPiece(piece.id, piece.version, 'publish');
      toast(withPieces ? `${target.name} y sus piezas ya están publicados` : `«${target.name}» publicado`);
      return true;
    } catch (e) {
      toast(writeErrorMessage(e instanceof ApiError ? e : new ApiError('network', 0)), 'error');
      return true; // something may have changed: let the caller reload
    }
  };
}
