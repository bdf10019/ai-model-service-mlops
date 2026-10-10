import { Children, cloneElement, createElement, isValidElement, useEffect, type ReactElement, type ReactNode } from 'react';

const untouched = new Set(['svg', 'input', 'textarea', 'select', 'option', 'pre', 'code', 'script', 'style']);

/** Wrap text runs, leaving controls and their hit areas in their original position. */
export function repelTypography(node: ReactNode): ReactNode {
  return Children.map(node, child => {
    if (typeof child === 'string' || typeof child === 'number') {
      return String(child).trim() ? createElement('motion-text', { className: 'cursor-text' }, child) : child;
    }
    if (!isValidElement(child)) return child;
    if (typeof child.type === 'string' && (untouched.has(child.type) || child.type === 'motion-text')) return child;
    const element = child as ReactElement<{ children?: ReactNode }>;
    if (element.props.children === undefined) return child;
    return cloneElement(element, undefined, repelTypography(element.props.children));
  });
}

export function useTextRepulsion() {
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return;
    const finePointer = window.matchMedia('(hover: hover) and (pointer: fine)');
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
    const root = document.getElementById('root');
    if (!root) return;
    let frame = 0;
    let dirty = true;
    let pointer: { x: number; y: number } | null = null;
    let targets: { node: HTMLElement | SVGElement; rect: DOMRect }[] = [];
    const active = new Set<HTMLElement | SVGElement>();
    const enabled = () => finePointer.matches && !reducedMotion.matches;
    const reset = () => {
      for (const node of active) {
        node.style.removeProperty('--repel-x');
        node.style.removeProperty('--repel-y');
      }
      active.clear();
    };
    const update = () => {
      frame = 0;
      if (!enabled() || !pointer) { reset(); return; }
      if (dirty) {
        targets = Array.from(root.querySelectorAll<HTMLElement | SVGElement>('motion-text, svg text'))
          .filter(node => !node.closest('button:disabled, [hidden], [inert]'))
          .map(node => ({ node, rect: node.getBoundingClientRect() }))
          .filter(({ rect }) => rect.width > 0 && rect.height > 0 && rect.bottom >= 0 && rect.top <= window.innerHeight);
        dirty = false;
      }
      const moved = new Set<HTMLElement | SVGElement>();
      for (const { node, rect } of targets) {
        const edgeX = Math.max(rect.left - pointer.x, 0, pointer.x - rect.right);
        const edgeY = Math.max(rect.top - pointer.y, 0, pointer.y - rect.bottom);
        const distance = Math.hypot(edgeX, edgeY);
        if (distance >= 105) continue;
        const dx = rect.left + rect.width / 2 - pointer.x;
        const dy = rect.top + rect.height / 2 - pointer.y;
        const length = Math.hypot(dx, dy);
        if (length < .5) continue;
        const strength = 3.5 * (1 - distance / 105) ** 2;
        node.style.setProperty('--repel-x', `${(dx / length * strength).toFixed(2)}px`);
        node.style.setProperty('--repel-y', `${(dy / length * strength).toFixed(2)}px`);
        moved.add(node);
      }
      for (const node of active) {
        if (!moved.has(node)) {
          node.style.removeProperty('--repel-x');
          node.style.removeProperty('--repel-y');
        }
      }
      active.clear();
      for (const node of moved) active.add(node);
    };
    const schedule = () => { if (!frame) frame = window.requestAnimationFrame(update); };
    const leave = () => { pointer = null; reset(); };
    const move = (event: PointerEvent) => {
      if (event.pointerType !== 'mouse' || !enabled()) { leave(); return; }
      pointer = { x: event.clientX, y: event.clientY };
      schedule();
    };
    const invalidate = () => { dirty = true; leave(); };
    const preferencesChanged = () => { dirty = true; leave(); };
    const observer = new MutationObserver(invalidate);
    observer.observe(root, { childList: true, subtree: true, characterData: true });
    root.addEventListener('pointermove', move, { passive: true });
    root.addEventListener('pointerleave', leave);
    window.addEventListener('blur', leave);
    window.addEventListener('scroll', invalidate, { passive: true, capture: true });
    window.addEventListener('resize', invalidate, { passive: true });
    window.addEventListener('beforeprint', leave);
    finePointer.addEventListener('change', preferencesChanged);
    reducedMotion.addEventListener('change', preferencesChanged);
    return () => {
      window.cancelAnimationFrame(frame);
      observer.disconnect();
      reset();
      root.removeEventListener('pointermove', move);
      root.removeEventListener('pointerleave', leave);
      window.removeEventListener('blur', leave);
      window.removeEventListener('scroll', invalidate, true);
      window.removeEventListener('resize', invalidate);
      window.removeEventListener('beforeprint', leave);
      finePointer.removeEventListener('change', preferencesChanged);
      reducedMotion.removeEventListener('change', preferencesChanged);
    };
  }, []);
}
