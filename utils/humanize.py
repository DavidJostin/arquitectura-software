"""Helpers para simular ritmo humano: retrasos aleatorios, ratón y scroll."""
from __future__ import annotations

import random
import time

from playwright.sync_api import Page


def jitter(min_s: float = 2.5, max_s: float = 5.0, page: Page | None = None) -> float:
    """Pausa aleatoria entre ``min_s`` y ``max_s`` segundos.

    Si se pasa ``page`` se usa ``page.wait_for_timeout``: en la API síncrona,
    ``time.sleep`` bloquea el bucle de eventos y las respuestas de red no
    llegan al handler hasta la siguiente llamada a Playwright.
    """
    delay = random.uniform(min_s, max_s)
    if page is not None:
        page.wait_for_timeout(delay * 1000)
    else:
        time.sleep(delay)
    return delay


def random_mouse_move(page: Page) -> None:
    """Mueve el ratón a un punto aleatorio de la zona central del viewport."""
    vp = page.viewport_size or {"width": 1366, "height": 768}
    x = random.randint(int(vp["width"] * 0.25), int(vp["width"] * 0.75))
    y = random.randint(int(vp["height"] * 0.3), int(vp["height"] * 0.8))
    page.mouse.move(x, y, steps=random.randint(8, 20))


def human_scroll(page: Page, min_px: int = 600, max_px: int = 1200) -> None:
    """Scroll progresivo en pasos pequeños e irregulares, con algún retroceso."""
    random_mouse_move(page)
    target = random.randint(min_px, max_px)
    done = 0
    while done < target:
        step = random.randint(80, 180)
        page.mouse.wheel(0, step)
        done += step
        page.wait_for_timeout(random.uniform(40, 160))

    # De vez en cuando una persona sube un poco para volver a mirar algo.
    if random.random() < 0.15:
        page.mouse.wheel(0, -random.randint(60, 200))
        page.wait_for_timeout(random.uniform(300, 800))
