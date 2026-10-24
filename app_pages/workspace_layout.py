"""Presentation-only responsive controls for the Clean workspace."""

from nicegui import ui


def set_mobile_view(layout, view: str) -> None:
    selected = "result" if view == "result" else "input"
    layout.classes(remove="cc-mobile-show-input cc-mobile-show-result",
                   add=f"cc-mobile-show-{selected}")


def bind_split_handle(layout, handle) -> None:
    handle.props("role=separator aria-orientation=vertical tabindex=0 "
                 "aria-label='Resize input and result panes' aria-valuemin=25 aria-valuemax=75 "
                 "aria-valuenow=50")
    ui.run_javascript("""
    (() => {
      const root = document.querySelector('.cc-clean-workspace');
      if (!root) return;
      const grip = root.querySelector('.cc-split-handle');
      if (!grip) return;
      let pct = 50;
      const apply = value => {
        pct = Math.max(25, Math.min(75, value));
        root.style.gridTemplateColumns =
          `minmax(0,calc(${pct}% - 4px)) 8px minmax(0,calc(${100-pct}% - 4px))`;
        grip.setAttribute('aria-valuenow', String(Math.round(pct)));
      };
      grip.addEventListener('pointerdown', event => {
        if (window.innerWidth < 1200) return;
        grip.setPointerCapture(event.pointerId);
        const move = e => {
          const box = root.getBoundingClientRect();
          apply((e.clientX - box.left) / box.width * 100);
        };
        const up = () => {
          grip.removeEventListener('pointermove', move);
          grip.removeEventListener('pointerup', up);
        };
        grip.addEventListener('pointermove', move);
        grip.addEventListener('pointerup', up);
      });
      grip.addEventListener('keydown', event => {
        if (!['ArrowLeft', 'ArrowRight', 'Home'].includes(event.key)) return;
        event.preventDefault();
        apply(event.key === 'Home' ? 50 : pct + (event.key === 'ArrowLeft' ? -5 : 5));
      });
    })();
    """)
