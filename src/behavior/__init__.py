"""
src/behavior/__init__.py
"""
from .simulator import BehaviorSimulator
from .mouse import move_mouse_to, hover_element
from .scroll import natural_page_browse
from .idle import simulate_idle, warm_up_session

__all__ = [
    "BehaviorSimulator",
    "move_mouse_to", "hover_element",
    "natural_page_browse",
    "simulate_idle", "warm_up_session",
]
