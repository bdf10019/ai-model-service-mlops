import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { repelTypography, useTextRepulsion } from './repellingText';

function Sample() {
  useTextRepulsion();
  return repelTypography(<button>可以操作的文字</button>);
}

describe('subtle text repulsion', () => {
  afterEach(() => { cleanup(); document.getElementById('root')?.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

  function setup(reduced = false) {
    vi.stubGlobal('matchMedia', (query: string) => ({
      matches: query.includes('reduced-motion') ? reduced : true,
      addEventListener: vi.fn(), removeEventListener: vi.fn(),
    }));
    let callback: FrameRequestCallback | undefined;
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation(cb => { callback = cb; return 1; });
    vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(() => {});
    const root = document.createElement('div');
    root.id = 'root';
    document.body.appendChild(root);
    render(<Sample />, { container: root });
    const node = screen.getByText('可以操作的文字');
    vi.spyOn(node, 'getBoundingClientRect').mockReturnValue({
      left: 100, top: 100, right: 200, bottom: 120, width: 100, height: 20, x: 100, y: 100, toJSON: () => ({}),
    });
    function move(x: number, y: number) {
      const event = new Event('pointermove', { bubbles: true });
      Object.defineProperties(event, { pointerType: { value: 'mouse' }, clientX: { value: x }, clientY: { value: y } });
      act(() => { root.dispatchEvent(event); callback?.(0); });
    }
    return { root, node, move };
  }

  test('moves text away from the pointer within the cap, keeps the button stationary, and restores on leave', () => {
    const { root, node, move } = setup();
    move(90, 110);
    const displacement = parseFloat(node.style.getPropertyValue('--repel-x'));
    expect(displacement).toBeGreaterThan(0);
    expect(displacement).toBeLessThanOrEqual(3.5);
    expect(screen.getByRole('button').style.getPropertyValue('--repel-x')).toBe('');
    move(210, 110);
    expect(parseFloat(node.style.getPropertyValue('--repel-x'))).toBeLessThan(0);
    fireEvent.pointerLeave(root);
    expect(node.style.getPropertyValue('--repel-x')).toBe('');
    expect(node.style.getPropertyValue('--repel-y')).toBe('');
  });

  test('leaves typography still when reduced motion is requested', () => {
    const { node, move } = setup(true);
    move(90, 110);
    expect(node.style.getPropertyValue('--repel-x')).toBe('');
    expect(node.style.getPropertyValue('--repel-y')).toBe('');
  });
});
