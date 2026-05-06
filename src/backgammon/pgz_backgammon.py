from __future__ import annotations

import os
import random
import time
from copy import deepcopy
from dataclasses import dataclass

# Force centered window placement at startup (center point aligns to display center).
os.environ["SDL_VIDEO_CENTERED"] = "1"
os.environ.pop("SDL_VIDEO_WINDOW_POS", None)

import pygame

TITLE = "Backgammon vs AI (Doubling Cube)"
WIDTH = 1200
HEIGHT = 720

POINT_COUNT = 24
HUMAN = 1
AI = -1

BOARD_LEFT = 80
BOARD_TOP = 80
BOARD_W = 1040
BOARD_H = 560
POINT_W = BOARD_W / 14
HALF_H = BOARD_H / 2
CHECKER_R = 20
DIE_SIZE = 52
DIE_GAP = 10
DICE_X = 250
DICE_Y = 14
CUBE_SIZE = 48
CUBE_DRAG_THRESHOLD = 16
BEAR_OFF_BORDER_W = 64
BEAR_OFF_BORDER_X = BOARD_LEFT + BOARD_W - BEAR_OFF_BORDER_W


def opponent(player: int) -> int:
    return -player


@dataclass
class Move:
    src: int  # -1 for bar
    dst: int  # 0..23 or 24 for borne off
    die: int


@dataclass
class StateSnapshot:
    points: list[list[int]]
    bar: dict[int, int]
    off: dict[int, int]
    turn: int
    dice: list[int]
    moves_left: list[int]
    selected_src: int | None
    drag_pos: tuple[int, int] | None
    moved_this_turn: dict[tuple[int, int], int]
    moved_last_turn: dict[tuple[int, int], int]
    winner: int | None
    match_points: dict[int, int]
    cube_value: int
    cube_owner: int | None
    double_pending: bool
    double_offerer: int | None
    last_roll: list[int]
    message: str
    ai_wait_until: float


class GameState:
    def __init__(self) -> None:
        self.points: list[list[int]] = [[] for _ in range(POINT_COUNT)]
        self.bar: dict[int, int] = {HUMAN: 0, AI: 0}
        self.off: dict[int, int] = {HUMAN: 0, AI: 0}
        self.turn: int = HUMAN
        self.dice: list[int] = []
        self.moves_left: list[int] = []
        self.selected_src: int | None = None
        self.drag_pos: tuple[int, int] | None = None
        self.moved_this_turn: dict[tuple[int, int], int] = {}
        self.moved_last_turn: dict[tuple[int, int], int] = {}
        self.winner: int | None = None
        self.match_points: dict[int, int] = {HUMAN: 0, AI: 0}

        self.cube_value = 1
        self.cube_owner: int | None = None
        self.double_pending = False
        self.double_offerer: int | None = None
        self.last_roll: list[int] = []

        self.message = "Your turn. Click dice to roll. Use UNDO button."
        self.ai_wait_until = 0.0
        self.cube_dragging = False
        self.cube_drag_start: tuple[int, int] | None = None
        self.cube_drag_pos: tuple[int, int] | None = None
        self.init_position()

    def init_position(self) -> None:
        self.points = [[] for _ in range(POINT_COUNT)]

        # Human moves high->low (home: 0..5). AI moves low->high (home: 18..23).
        self._set_point(0, AI, 2)
        self._set_point(11, AI, 5)
        self._set_point(16, AI, 3)
        self._set_point(18, AI, 5)

        self._set_point(23, HUMAN, 2)
        self._set_point(12, HUMAN, 5)
        self._set_point(7, HUMAN, 3)
        self._set_point(5, HUMAN, 5)

    def _set_point(self, idx: int, player: int, count: int) -> None:
        self.points[idx] = [player] * count

    def roll_dice(self) -> None:
        d1 = random.randint(1, 6)
        d2 = random.randint(1, 6)
        if d1 == d2:
            self.dice = [d1, d1, d1, d1]
        else:
            self.dice = [d1, d2]
        self.last_roll = [d1, d2]
        self.moves_left = self.dice[:]

    def all_in_home(self, player: int) -> bool:
        if self.bar[player] > 0:
            return False
        if player == HUMAN:
            outside = range(6, 24)
        else:
            outside = range(18)
        for i in outside:
            if self.points[i] and self.points[i][0] == player:
                return False
        return True

    def direction(self, player: int) -> int:
        return -1 if player == HUMAN else 1

    def entry_point(self, player: int, die: int) -> int:
        if player == HUMAN:
            return 24 - die
        return die - 1

    def target_point(self, player: int, src: int, die: int) -> int:
        return src + self.direction(player) * die

    def can_land(self, player: int, dst: int) -> bool:
        if dst < 0 or dst >= 24:
            return False
        stack = self.points[dst]
        return not (len(stack) >= 2 and stack[0] == opponent(player))

    def legal_moves_for_die(self, player: int, die: int) -> list[Move]:
        moves: list[Move] = []

        if self.bar[player] > 0:
            ep = self.entry_point(player, die)
            if self.can_land(player, ep):
                moves.append(Move(-1, ep, die))
            return moves

        for src in range(24):
            if not self.points[src] or self.points[src][0] != player:
                continue
            dst = self.target_point(player, src, die)
            if 0 <= dst <= 23:
                if self.can_land(player, dst):
                    moves.append(Move(src, dst, die))
                continue

            # bearing off
            if self.all_in_home(player):
                if player == HUMAN:
                    exact = src - die == -1
                    over = src - die < -1
                    farther = any(self.points[p] and self.points[p][0] == player for p in range(src + 1, 6))
                else:
                    exact = src + die == 24
                    over = src + die > 24
                    farther = any(self.points[p] and self.points[p][0] == player for p in range(18, src))
                if exact or (over and not farther):
                    moves.append(Move(src, 24, die))

        return moves

    def legal_moves(self, player: int) -> list[Move]:
        result: list[Move] = []
        used: set[tuple[int, int, int]] = set()
        for die in sorted(set(self.moves_left), reverse=True):
            for m in self.legal_moves_for_die(player, die):
                k = (m.src, m.dst, m.die)
                if k not in used:
                    used.add(k)
                    result.append(m)
        return result

    def apply_move(self, move: Move) -> None:
        player = self.turn
        if move.src == -1:
            self.bar[player] -= 1
        else:
            self.points[move.src].pop()

        if move.dst == 24:
            self.off[player] += 1
        else:
            stack = self.points[move.dst]
            if len(stack) == 1 and stack[0] == opponent(player):
                stack.pop()
                self.bar[opponent(player)] += 1
            stack.append(player)

        self.moves_left.remove(move.die)
        if move.dst != 24:
            key = (player, move.dst)
            self.moved_this_turn[key] = self.moved_this_turn.get(key, 0) + 1

        if self.off[player] >= 15:
            self.set_winner(player)

    def end_turn_if_needed(self) -> None:
        if self.winner is not None:
            return
        if not self.moves_left:
            self.end_turn()
            return
        if not self.legal_moves(self.turn):
            self.message = "No legal moves. Turn ends."
            self.end_turn()

    def end_turn(self) -> None:
        # Keep a snapshot of the just-finished turn so the opponent can
        # distinguish which checkers were moved most recently.
        self.moved_last_turn = self.moved_this_turn.copy()
        self.turn = opponent(self.turn)
        self.dice = []
        self.moves_left = []
        self.moved_this_turn = {}
        self.selected_src = None
        self.drag_pos = None
        self.cube_dragging = False
        self.cube_drag_start = None
        self.cube_drag_pos = None
        clear_undo()
        if self.turn == HUMAN:
            self.message = "Your turn. Click dice to roll, drag cube to double. Use UNDO button."
        else:
            self.message = "AI turn."
            self.ai_wait_until = time.monotonic() + 0.5

    def can_offer_double(self, player: int) -> bool:
        if self.double_pending or self.winner is not None:
            return False
        return self.cube_owner in (None, player)

    def offer_double(self, player: int) -> None:
        self.double_pending = True
        self.double_offerer = player
        self.message = "Double offered. Click TAKE or DROP."

    def accept_double(self) -> None:
        if not self.double_pending or self.double_offerer is None:
            return
        off = self.double_offerer
        self.cube_value *= 2
        self.cube_owner = opponent(off)
        self.double_pending = False
        self.double_offerer = None
        self.message = "Double accepted. Game continues."

    def reject_double(self) -> None:
        if not self.double_pending or self.double_offerer is None:
            return
        off = self.double_offerer
        self.set_winner(off, with_bonus=False)
        self.double_pending = False
        self.double_offerer = None

    def _win_multiplier(self, winner: int) -> int:
        loser = opponent(winner)
        if self.off[loser] > 0:
            return 1
        winner_home = range(6) if winner == HUMAN else range(18, 24)
        loser_has_in_winner_home = any(self.points[idx] and self.points[idx][0] == loser for idx in winner_home)
        if self.bar[loser] > 0 or loser_has_in_winner_home:
            return 3
        return 2

    def set_winner(self, player: int, with_bonus: bool = True) -> None:
        if self.winner is not None:
            return
        self.winner = player
        mult = self._win_multiplier(player) if with_bonus else 1
        self.match_points[player] += self.cube_value * mult

    def snapshot(self) -> StateSnapshot:
        return StateSnapshot(
            points=deepcopy(self.points),
            bar=self.bar.copy(),
            off=self.off.copy(),
            turn=self.turn,
            dice=self.dice[:],
            moves_left=self.moves_left[:],
            selected_src=self.selected_src,
            drag_pos=self.drag_pos,
            moved_this_turn=self.moved_this_turn.copy(),
            moved_last_turn=self.moved_last_turn.copy(),
            winner=self.winner,
            match_points=self.match_points.copy(),
            cube_value=self.cube_value,
            cube_owner=self.cube_owner,
            double_pending=self.double_pending,
            double_offerer=self.double_offerer,
            last_roll=self.last_roll[:],
            message=self.message,
            ai_wait_until=self.ai_wait_until,
        )

    def restore(self, snap: StateSnapshot) -> None:
        self.points = deepcopy(snap.points)
        self.bar = snap.bar.copy()
        self.off = snap.off.copy()
        self.turn = snap.turn
        self.dice = snap.dice[:]
        self.moves_left = snap.moves_left[:]
        self.selected_src = snap.selected_src
        self.drag_pos = snap.drag_pos
        self.moved_this_turn = snap.moved_this_turn.copy()
        self.moved_last_turn = snap.moved_last_turn.copy()
        self.winner = snap.winner
        self.match_points = snap.match_points.copy()
        self.cube_value = snap.cube_value
        self.cube_owner = snap.cube_owner
        self.double_pending = snap.double_pending
        self.double_offerer = snap.double_offerer
        self.last_roll = snap.last_roll[:]
        self.message = snap.message
        self.ai_wait_until = snap.ai_wait_until


state = GameState()
undo_stack: list[StateSnapshot] = []


def push_undo() -> None:
    undo_stack.append(state.snapshot())


def clear_undo() -> None:
    undo_stack.clear()


def undo_last_action() -> None:
    if not undo_stack:
        state.message = "Nothing to undo."
        return
    state.restore(undo_stack.pop())
    state.message = "Undo completed."


def point_to_x(idx: int) -> float:
    # Mirror on 12 playable columns first, then apply bar gap.
    col = idx if idx < 12 else 23 - idx
    col = 11 - col
    if col >= 6:
        col += 1
    return BOARD_LEFT + POINT_W * (col + 0.5)


def checker_y(idx: int, depth: int) -> float:
    if idx < 12:
        return BOARD_TOP + BOARD_H - CHECKER_R - depth * (CHECKER_R * 1.8)
    return BOARD_TOP + CHECKER_R + depth * (CHECKER_R * 1.8)


def bar_checker_pos(player: int, depth: int) -> tuple[float, float]:
    x = BOARD_LEFT + POINT_W * 6.5
    step = CHECKER_R * 1.8
    if player == HUMAN:
        y = BOARD_TOP + BOARD_H - CHECKER_R * 1.4 - depth * step
    else:
        y = BOARD_TOP + CHECKER_R * 1.4 + depth * step
    return x, y


def off_checker_pos(player: int, depth: int) -> tuple[float, float]:
    x = BEAR_OFF_BORDER_X + BEAR_OFF_BORDER_W / 2
    step = CHECKER_R * 1.8
    if player == HUMAN:
        y = BOARD_TOP + BOARD_H - CHECKER_R - depth * step
    else:
        y = BOARD_TOP + CHECKER_R + depth * step
    return x, y


def human_bar_hit(pos: tuple[int, int]) -> bool:
    if state.bar[HUMAN] <= 0:
        return False
    x, y = bar_checker_pos(HUMAN, 0)
    hit_w = CHECKER_R * 2.4
    hit_h = CHECKER_R * 7.0
    rect = Rect((x - hit_w / 2, y - hit_h + CHECKER_R), (hit_w, hit_h))
    return rect.collidepoint(pos)


def draw_board() -> None:
    screen.fill((18, 45, 62))
    screen.draw.filled_rect(Rect((BOARD_LEFT, BOARD_TOP), (BOARD_W, BOARD_H)), (209, 164, 111))
    screen.draw.filled_rect(Rect((BOARD_LEFT + POINT_W * 6, BOARD_TOP), (POINT_W, BOARD_H)), (120, 92, 60))
    screen.draw.filled_rect(Rect((BOARD_LEFT + POINT_W * 13 + 1, BOARD_TOP), (POINT_W, BOARD_H)), (120, 92, 60))

    for i in range(12):
        x0 = BOARD_LEFT + POINT_W * i
        if i >= 6:
            x0 += POINT_W
        x1 = x0 + POINT_W
        color_top = (140, 60, 35) if i % 2 == 0 else (233, 209, 170)
        color_bottom = (233, 209, 170) if i % 2 == 0 else (140, 60, 35)
        pygame.draw.polygon(
            screen.surface,
            color_top,
            [(x0, BOARD_TOP), (x1, BOARD_TOP), ((x0 + x1) / 2, BOARD_TOP + HALF_H)],
        )
        pygame.draw.polygon(
            screen.surface,
            color_bottom,
            [(x0, BOARD_TOP + BOARD_H), (x1, BOARD_TOP + BOARD_H), ((x0 + x1) / 2, BOARD_TOP + HALF_H)],
        )


def draw_checkers() -> None:
    ai_movable_sources: set[int] = set()
    if state.turn == AI and state.moves_left:
        ai_movable_sources = {m.src for m in state.legal_moves(AI) if m.src >= 0}

    for idx in range(24):
        stack = state.points[idx]
        visible_stack = stack
        if state.selected_src == idx and state.drag_pos is not None and stack and stack[-1] == HUMAN:
            # While dragging, hide the moving top checker at the source point.
            visible_stack = stack[:-1]
        for depth, player in enumerate(visible_stack[:5]):
            x = point_to_x(idx)
            y = checker_y(idx, depth)
            color = (245, 245, 245) if player == HUMAN else (32, 32, 32)
            outline = (30, 30, 30) if player == HUMAN else (220, 220, 220)
            screen.draw.filled_circle((x, y), CHECKER_R, color)
            screen.draw.circle((x, y), CHECKER_R, outline)

            moved_count = state.moved_this_turn.get((player, idx), 0)
            if moved_count == 0 and player != state.turn:
                moved_count = state.moved_last_turn.get((player, idx), 0)
            top_start = len(visible_stack[:5]) - moved_count
            is_last_moved = moved_count > 0 and depth >= max(0, top_start)
            if is_last_moved:
                marker = (255, 215, 0) if player == HUMAN else (80, 190, 255)
                screen.draw.circle((x, y), CHECKER_R + 4, marker)

            is_ai_candidate = (
                state.turn == AI and player == AI and idx in ai_movable_sources and depth == len(visible_stack[:5]) - 1
            )
            if is_ai_candidate:
                screen.draw.circle((x, y), CHECKER_R + 7, (255, 120, 120))

        if len(visible_stack) > 5:
            x = point_to_x(idx)
            y = checker_y(idx, 4)
            screen.draw.text(str(len(visible_stack)), center=(x, y), color="red", fontsize=28)

    for player in (AI, HUMAN):
        count = state.bar[player]
        if count <= 0:
            continue

        color = (245, 245, 245) if player == HUMAN else (32, 32, 32)
        outline = (30, 30, 30) if player == HUMAN else (220, 220, 220)
        for depth in range(min(count, 5)):
            x, y = bar_checker_pos(player, depth)
            screen.draw.filled_circle((x, y), CHECKER_R, color)
            screen.draw.circle((x, y), CHECKER_R, outline)

        if count > 5:
            x, y = bar_checker_pos(player, 4)
            screen.draw.text(str(count), center=(x, y), color="red", fontsize=28)

    for player in (AI, HUMAN):
        count = state.off[player]
        if count <= 0:
            continue

        color = (245, 245, 245) if player == HUMAN else (32, 32, 32)
        outline = (30, 30, 30) if player == HUMAN else (220, 220, 220)
        for depth in range(min(count, 5)):
            x, y = off_checker_pos(player, depth)
            screen.draw.filled_circle((x, y), CHECKER_R, color)
            screen.draw.circle((x, y), CHECKER_R, outline)

        if count > 5:
            x, y = off_checker_pos(player, 4)
            screen.draw.text(str(count), center=(x, y), color="red", fontsize=28)


def reset_button_rect() -> Rect:
    return Rect((WIDTH - 130, 18), (100, 36))


def replay_button_rect() -> Rect:
    return Rect((WIDTH - 250, 18), (100, 36))


def undo_button_rect() -> Rect:
    return Rect((WIDTH - 370, 18), (100, 36))


def take_button_rect() -> Rect:
    return Rect((680, 610), (120, 42))


def drop_button_rect() -> Rect:
    return Rect((812, 610), (120, 42))


def cube_rect() -> Rect:
    center_x = BOARD_LEFT + POINT_W * 6.5
    if state.cube_owner == AI:
        center_y = BOARD_TOP + CHECKER_R * 2.2
    elif state.cube_owner == HUMAN:
        center_y = BOARD_TOP + BOARD_H - CHECKER_R * 2.2
    else:
        center_y = HEIGHT / 2
    return Rect((center_x - CUBE_SIZE / 2, center_y - CUBE_SIZE / 2), (CUBE_SIZE, CUBE_SIZE))


def draw_move_hints() -> None:
    if state.turn != HUMAN or not state.moves_left or state.selected_src is None:
        return

    legal = state.legal_moves(HUMAN)
    moves = [m for m in legal if m.src == state.selected_src]
    if not moves:
        return

    dst_points = {m.dst for m in moves if m.dst != 24}
    for dst in dst_points:
        x = point_to_x(dst)
        y = checker_y(dst, min(len(state.points[dst]), 4))
        screen.draw.filled_circle((x, y), CHECKER_R - 5, (80, 170, 255))
        screen.draw.circle((x, y), CHECKER_R - 2, (245, 245, 120))


def draw_drag_checker() -> None:
    if state.selected_src is None or state.drag_pos is None:
        return
    x, y = state.drag_pos
    screen.draw.filled_circle((x, y), CHECKER_R, (245, 245, 245))
    screen.draw.circle((x, y), CHECKER_R, (30, 30, 30))


def draw_ui() -> None:
    screen.draw.text(f"Turn: {'YOU' if state.turn == HUMAN else 'AI'}", (40, 20), color="white", fontsize=36)
    draw_dice_panel()
    screen.draw.text(
        f"POINTS YOU:{state.match_points[HUMAN]}  AI:{state.match_points[AI]}", (40, 56), color="white", fontsize=28
    )

    cube = cube_rect()
    cube_fill = (
        (245, 245, 245)
        if state.can_offer_double(HUMAN) and state.turn == HUMAN and not state.moves_left
        else (210, 210, 210)
    )
    if state.cube_dragging:
        cube = Rect(
            (state.cube_drag_pos[0] - CUBE_SIZE / 2, state.cube_drag_pos[1] - CUBE_SIZE / 2), (CUBE_SIZE, CUBE_SIZE)
        )
    screen.draw.filled_rect(cube, cube_fill)
    screen.draw.rect(cube, (35, 35, 35))
    screen.draw.text(str(state.cube_value), center=cube.center, color="black", fontsize=34)

    screen.draw.text(f"BAR YOU:{state.bar[HUMAN]}  AI:{state.bar[AI]}", (40, 660), color="white", fontsize=30)
    screen.draw.text(f"OFF YOU:{state.off[HUMAN]}  AI:{state.off[AI]}", (360, 660), color="white", fontsize=30)
    screen.draw.text(state.message, (700, 660), color="yellow", fontsize=28)
    if state.double_pending and state.double_offerer == AI:
        take_rect = take_button_rect()
        drop_rect = drop_button_rect()
        screen.draw.filled_rect(take_rect, (45, 130, 62))
        screen.draw.rect(take_rect, (235, 255, 235))
        screen.draw.text("TAKE", center=take_rect.center, color="white", fontsize=24)
        screen.draw.filled_rect(drop_rect, (170, 62, 62))
        screen.draw.rect(drop_rect, (255, 235, 235))
        screen.draw.text("DROP", center=drop_rect.center, color="white", fontsize=24)

    reset_rect = reset_button_rect()
    screen.draw.filled_rect(reset_rect, (185, 52, 52))
    screen.draw.rect(reset_rect, (255, 245, 245))
    screen.draw.text("RESET", center=reset_rect.center, color="white", fontsize=22)

    undo_rect = undo_button_rect()
    undo_enabled = state.turn == HUMAN and not state.double_pending and state.winner is None and bool(undo_stack)
    undo_fill = (55, 120, 165) if undo_enabled else (110, 120, 130)
    undo_border = (225, 240, 250) if undo_enabled else (170, 175, 180)
    screen.draw.filled_rect(undo_rect, undo_fill)
    screen.draw.rect(undo_rect, undo_border)
    screen.draw.text("UNDO", center=undo_rect.center, color="white", fontsize=22)

    if state.winner is not None:
        replay_rect = replay_button_rect()
        screen.draw.filled_rect(replay_rect, (45, 130, 62))
        screen.draw.rect(replay_rect, (235, 255, 235))
        screen.draw.text("REPLAY", center=replay_rect.center, color="white", fontsize=20)

    if state.winner is not None:
        txt = "YOU WIN" if state.winner == HUMAN else "AI WIN"
        screen.draw.text(txt, center=(WIDTH / 2, HEIGHT / 2), fontsize=90, color="gold")


def cube_owner_text() -> str:
    if state.cube_owner is None:
        return "CENTER"
    return "YOU" if state.cube_owner == HUMAN else "AI"


def die_rects(base_x: int, count: int = 2) -> list[Rect]:
    return [Rect((base_x + i * (DIE_SIZE + DIE_GAP), DICE_Y), (DIE_SIZE, DIE_SIZE)) for i in range(count)]


def draw_die_face(rect: Rect, value: int | None, enabled: bool) -> None:
    fill = (250, 250, 250) if enabled else (185, 185, 185)
    pip = (20, 20, 20) if enabled else (120, 120, 120)
    border = (40, 40, 40)
    screen.draw.filled_rect(rect, fill)
    screen.draw.rect(rect, border)
    if value is None:
        return

    cx, cy = rect.center
    off = DIE_SIZE * 0.25
    spots = {
        "tl": (cx - off, cy - off),
        "tr": (cx + off, cy - off),
        "ml": (cx - off, cy),
        "mr": (cx + off, cy),
        "bl": (cx - off, cy + off),
        "br": (cx + off, cy + off),
        "c": (cx, cy),
    }
    layout = {
        1: ["c"],
        2: ["tl", "br"],
        3: ["tl", "c", "br"],
        4: ["tl", "tr", "bl", "br"],
        5: ["tl", "tr", "c", "bl", "br"],
        6: ["tl", "tr", "ml", "mr", "bl", "br"],
    }
    for key in layout.get(value, []):
        screen.draw.filled_circle(spots[key], 5, pip)


def draw_dice_panel() -> None:
    def panel_values(values: list[int]) -> list[int | None]:
        shown = values[:2]
        return shown + [None] * (2 - len(shown))

    values = panel_values(state.last_roll) if state.last_roll else [None, None]
    if state.moves_left:
        values = panel_values(state.moves_left)

    # screen.draw.text("DICE", (DICE_X - 72, DICE_Y + 12), color="white", fontsize=28)

    enabled = state.turn == HUMAN and not state.moves_left and not state.double_pending and state.winner is None
    for i, rect in enumerate(die_rects(DICE_X)):
        draw_die_face(rect, values[i], enabled)


def draw() -> None:
    draw_board()
    draw_move_hints()
    draw_checkers()
    draw_drag_checker()
    draw_ui()


def point_from_mouse(pos: tuple[int, int]) -> int | None:
    x, y = pos
    if not (BOARD_LEFT <= x <= BOARD_LEFT + BOARD_W and BOARD_TOP <= y <= BOARD_TOP + BOARD_H):
        return None
    colf = (x - BOARD_LEFT) / POINT_W
    col = int(colf)
    if col == 6:
        return None
    if col > 6:
        col -= 1
    col = 11 - col
    if y > BOARD_TOP + HALF_H:
        idx = col
    else:
        idx = 23 - col
    if 0 <= idx <= 23:
        return idx
    return None


def on_key_down(key) -> None:
    return


def on_mouse_down(pos) -> None:
    if (
        undo_button_rect().collidepoint(pos)
        and state.turn == HUMAN
        and not state.double_pending
        and state.winner is None
    ):
        undo_last_action()
        return

    if reset_button_rect().collidepoint(pos):
        reset_game()
        return

    if state.winner is not None and replay_button_rect().collidepoint(pos):
        reset_game()
        return

    if state.double_pending and state.double_offerer == AI:
        if take_button_rect().collidepoint(pos):
            state.accept_double()
            return
        if drop_button_rect().collidepoint(pos):
            state.reject_double()
            return

    if (
        state.turn == HUMAN
        and not state.moves_left
        and not state.double_pending
        and state.winner is None
        and state.can_offer_double(HUMAN)
        and cube_rect().collidepoint(pos)
    ):
        state.cube_dragging = True
        state.cube_drag_start = pos
        state.cube_drag_pos = pos
        state.message = "Drag cube away and release to offer double."
        return

    if (
        state.turn == HUMAN
        and not state.moves_left
        and not state.double_pending
        and state.winner is None
        and any(r.collidepoint(pos) for r in die_rects(DICE_X))
    ):
        push_undo()
        state.roll_dice()
        state.message = "Drag a checker to move."
        state.end_turn_if_needed()
        return

    if state.winner is not None or state.double_pending:
        return
    if state.turn != HUMAN or not state.moves_left:
        return

    if state.bar[HUMAN] > 0:
        if not human_bar_hit(pos):
            state.message = "Re-enter checkers from the bar first."
            return
        legal = state.legal_moves(HUMAN)
        if not any(m.src == -1 for m in legal):
            state.message = "No legal bar entry."
            return
        state.selected_src = -1
        state.drag_pos = pos
        state.message = "Dragging from bar."
        return

    clicked = point_from_mouse(pos)
    if clicked is None:
        return

    if state.points[clicked] and state.points[clicked][0] == HUMAN:
        legal = state.legal_moves(HUMAN)
        if not any(m.src == clicked for m in legal):
            state.message = "That checker cannot move."
            return
        state.selected_src = clicked
        state.drag_pos = pos
        state.message = f"Dragging from point {clicked + 1}."


def on_mouse_move(pos, rel, buttons) -> None:
    if state.cube_dragging:
        state.cube_drag_pos = pos
        return
    if state.selected_src is not None:
        state.drag_pos = pos


def on_mouse_up(pos) -> None:
    if state.cube_dragging:
        start = state.cube_drag_start
        state.cube_dragging = False
        state.cube_drag_start = None
        state.cube_drag_pos = None
        if start is None:
            return
        moved = abs(pos[0] - start[0]) + abs(pos[1] - start[1])
        if moved >= CUBE_DRAG_THRESHOLD and not cube_rect().collidepoint(pos):
            push_undo()
            state.offer_double(HUMAN)
        else:
            state.message = "Drag cube to offer double."
        return

    if state.selected_src is None:
        return

    legal = state.legal_moves(HUMAN)
    dst = point_from_mouse(pos)
    if dst is None:
        # Bear off by dropping anywhere to the right of the off area.
        if pos[0] >= BEAR_OFF_BORDER_X:
            dst = 24

    candidates = [m for m in legal if m.src == state.selected_src and m.dst == dst]
    if not candidates:
        state.selected_src = None
        state.drag_pos = None
        state.message = "Cannot move to that point."
        return

    # When multiple dice could make same move, use smallest die.
    mv = sorted(candidates, key=lambda m: m.die)[0]
    push_undo()
    state.apply_move(mv)
    state.selected_src = None
    state.drag_pos = None
    state.end_turn_if_needed()


def ai_move_score(m: Move) -> int:
    score = 0
    if m.dst == 24:
        score += 80
    else:
        # prefer advancing toward home and hitting blots
        score += m.dst * 2
        dst_stack = state.points[m.dst]
        if len(dst_stack) == 1 and dst_stack[0] == HUMAN:
            score += 40
    return score


def ai_turn_step() -> None:
    if state.winner is not None:
        return

    if state.double_pending:
        if state.double_offerer == HUMAN:
            # Simple policy: accept unless far behind in race.
            if ai_race_pip() <= human_race_pip() + 12:
                state.accept_double()
            else:
                state.reject_double()
        return

    if state.turn != AI:
        return

    if time.monotonic() < state.ai_wait_until:
        return

    if not state.moves_left:
        # Simple doubling policy.
        if state.can_offer_double(AI) and ai_race_pip() + 10 < human_race_pip() and random.random() < 0.25:
            state.offer_double(AI)
            return

        state.roll_dice()
        state.message = "AI is thinking..."
        state.ai_wait_until = time.monotonic() + 0.5
        return

    legal = state.legal_moves(AI)
    if not legal:
        state.end_turn()
        return

    best = max(legal, key=ai_move_score)
    state.apply_move(best)
    state.ai_wait_until = time.monotonic() + 0.5
    state.end_turn_if_needed()


def ai_race_pip() -> int:
    total = 0
    for i in range(24):
        stack = state.points[i]
        if stack and stack[0] == AI:
            total += len(stack) * (24 - i)
    total += state.bar[AI] * 25
    return total


def human_race_pip() -> int:
    total = 0
    for i in range(24):
        stack = state.points[i]
        if stack and stack[0] == HUMAN:
            total += len(stack) * (i + 1)
    total += state.bar[HUMAN] * 25
    return total


def update() -> None:
    ai_turn_step()


def reset_game() -> None:
    global state
    prev_points = state.match_points.copy()
    state = GameState()
    state.match_points = prev_points
    clear_undo()
