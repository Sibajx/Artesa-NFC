// The unlocked original certificate, alive: it tilts toward the pointer or the phone
// (with a moving glare), arrives with a spring, and a shockwave leaves the seal the
// moment it opens. The certificate itself is an SVG shown inside <img>: its own
// motion (turning tesseract, light along the frame) lives in the SVG and needs no script.
//
// All of this is dressing. Reduced motion gets the plain image, and nothing here is
// needed to read or use the certificate.
import { useEffect, useRef, useState, type PointerEvent } from "react";
import {
  LazyMotion,
  domAnimation,
  m,
  useMotionTemplate,
  useMotionValue,
  useReducedMotion,
  useSpring,
  useTransform,
} from "motion/react";
import { REST, orientationTilt, pointerTilt, type Tilt } from "@/lib/tilt";

// Where the seal sits on the sheet (1200 x 1600): the shockwave starts there.
const SEAL = { left: "50%", top: "66%" };
const SPRING = { stiffness: 150, damping: 17, mass: 0.6 };

type OrientationEvents = typeof DeviceOrientationEvent & {
  requestPermission?: () => Promise<"granted" | "denied">;
};

export function TiltCertificate({ svg, alt }: { svg: string; alt: string }) {
  const reduced = useReducedMotion();
  const src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
  const box = useRef<HTMLDivElement>(null);
  const x = useMotionValue(0);
  const y = useMotionValue(0);
  const sx = useSpring(x, SPRING);
  const sy = useSpring(y, SPRING);
  const rotateY = useTransform(sx, [-1, 1], [-9, 9]);
  const rotateX = useTransform(sy, [-1, 1], [9, -9]);
  const gx = useTransform(sx, [-1, 1], [18, 82]);
  const gy = useTransform(sy, [-1, 1], [12, 88]);
  const glare = useMotionTemplate`radial-gradient(circle at ${gx}% ${gy}%, rgba(255,255,255,0.34), rgba(255,255,255,0) 58%)`;
  // Only touch screens ask for permission (iOS does, and only after a tap); it is known from the
  // start, so no effect sets it, and a desktop never shows the button.
  const [needsPermission, setNeedsPermission] = useState(
    () =>
      typeof window !== "undefined" &&
      window.matchMedia("(pointer: coarse)").matches &&
      "DeviceOrientationEvent" in window &&
      typeof (window.DeviceOrientationEvent as OrientationEvents).requestPermission === "function",
  );
  const [gyro, setGyro] = useState(false);

  const aim = (tilt: Tilt) => {
    x.set(tilt.x);
    y.set(tilt.y);
  };

  // Phones: lean it to tilt it. iOS asks first, and only from a tap.
  useEffect(() => {
    if (reduced || typeof window === "undefined" || !("DeviceOrientationEvent" in window)) return;
    if (needsPermission && !gyro) return;
    const onTilt = (event: DeviceOrientationEvent) => {
      if (event.beta === null && event.gamma === null) return;
      x.set(orientationTilt(event.beta, event.gamma).x);
      y.set(orientationTilt(event.beta, event.gamma).y);
    };
    window.addEventListener("deviceorientation", onTilt);
    return () => window.removeEventListener("deviceorientation", onTilt);
  }, [reduced, needsPermission, gyro, x, y]);

  async function askForMotion() {
    const events = window.DeviceOrientationEvent as OrientationEvents;
    try {
      if ((await events.requestPermission?.()) === "granted") {
        setGyro(true);
      }
    } catch {
      // Denied or unavailable: the pointer still works.
    }
    setNeedsPermission(false);
  }

  function onPointer(event: PointerEvent<HTMLDivElement>) {
    const rect = box.current?.getBoundingClientRect();
    if (rect) aim(pointerTilt(event.clientX, event.clientY, rect));
  }

  if (reduced) {
    return <img className="original__design" src={src} alt={alt} data-state="original-design" />;
  }

  return (
    <LazyMotion features={domAnimation} strict>
      <m.div
        className="original__stage"
        initial={{ opacity: 0, y: 28, scale: 0.94 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        transition={{ type: "spring", stiffness: 90, damping: 16 }}
      >
        <div
          ref={box}
          className="original__tilt"
          role="presentation"
          onPointerMove={onPointer}
          onPointerDown={onPointer}
          onPointerLeave={() => aim(REST)}
          onPointerUp={(event) => event.pointerType !== "mouse" && aim(REST)}
        >
          <m.div className="original__plate" style={{ rotateX, rotateY }}>
            <m.img
              className="original__design"
              src={src}
              alt={alt}
              data-state="original-design"
              initial={{ filter: "brightness(1.9) saturate(1.3)" }}
              animate={{ filter: "brightness(1) saturate(1)" }}
              transition={{ duration: 1.1, ease: "easeOut" }}
              draggable={false}
            />
            <m.span className="original__glare" aria-hidden="true" style={{ background: glare }} />
            {[0, 1, 2].map((i) => (
              <m.span
                key={i}
                className="original__shock"
                aria-hidden="true"
                style={SEAL}
                initial={{ scale: 0.15, opacity: 0.6 }}
                animate={{ scale: 5.5, opacity: 0 }}
                transition={{ duration: 1.7, delay: 0.25 + i * 0.2, ease: "easeOut" }}
              />
            ))}
          </m.div>
        </div>
        {needsPermission && !gyro && (
          <button
            type="button"
            className="link-button original__motion"
            onClick={() => void askForMotion()}
          >
            Mover el certificado con tu celular
          </button>
        )}
      </m.div>
    </LazyMotion>
  );
}
