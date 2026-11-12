import os
import math
import io
from functools import lru_cache
from random import randint

import discord
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont

load_dotenv()       # Loads .env for bot token (DISCORD_TOKEN)

bot = discord.Bot()

DICE = ["d4", "d6", "d8", "d10", "d12", "d20"]

# --- Rolling logic ---
def roll_die(sides: int) -> int:
    return randint(1, sides)

def multiroll(die: str, count: int):
    sides = int(die[1:])
    rolls = [randint(1, sides) for _ in range(count)]
    return rolls, sum(rolls)

# --- Tuning knobs ---
# Image layout
TILE = 128                  # Width and height of one die tile in px
SS = 4                      # Supersampling: Draw at this multiplier, then scale to size. Smoothens edges.
GRID_COLS = 10              # Dice per row in multiroll composite image
TRANSPARENT = (0, 0, 0, 0)  # Background of tiles and composites

# Line widths in tile px (multiplied by SS when drawing)
OUTLINE_WIDTH = 5           # Die outline
FACET_WIDTH = 3             # Inner facet lines
TEXT_STROKE_WIDTH = 2       # Edge around numbers, keeps it readable over facets.

# Shapes
DIE_RADIUS = 0.46           # Fraction of the tile: distance from the die's center to it's corners.
POLYGON_START_ANGLE = -90   # Degrees, puts the first corner straight up.
D4_DROP = 0.25              # Fraction of the radius: shifts triangle down so it looks vertically centered
D6_MARGIN = 0.06            # Fraction of the tile: empty space between the square and the tile edge.
D6_CORNER_RADIUS = 0.16     # Fraction of the tile: Square corner roundness
D10_HEIGHT = 0.85           # Diamond half-height as a fraction of it's half-width (below 1 = wider than tall)
D8_WIDTH = 0.7              # Kite half-width as a fraction of the radius (lower = narrower)
D8_SHOULDER = 0.15          # Fraction of the radius: how far above center the kite's side corners sit.
D12_DROP = 0.1              # Fractin radius: shifts the pentagon down, same idea as D4_DROP

# Facets
D10_EQUATOR_SPREAD = 0.4    # Fraction of half-width: how far from center the two equator points sit.
D8_MEET_POINT = 0.35        # How far down from center to the bottom corner the three faces meet (0 to 1)
D12_INNER_SCALE = 0.5       # Size of the inner pentagon relative to the outline.
D20_INNER_SCALE = 0.55      # Size of the inner triangle's hexagon relative to the outline.

# Number font size as a fraction of the tile: (one digit, two digit)
FONT_SCALE_DEFAULT = (0.40, 0.30)
FONT_SCALE = {              # Font scale overrides for dice with busy or cramped centers.
    4: (0.26, 0.26),
    20: (0.34, 0.25),
}

# Dice colors
PURPLE = {  # Purple for normal rolls.
    "fill": (92, 50, 160, 255),
    "outline": (10, 6, 18, 255),
    "facet": (52, 26, 100, 255),
    "text": (255, 255, 255, 255),
}
GOLD = {    # Gold when rolling a max rolls.
    "fill": (212, 160, 23, 255),
    "outline": (22, 14, 0, 255),
    "facet": (130, 92, 6, 255),
    "text": (255, 255, 255, 255),
}
RED = {     # Red when rolling a NAT1.
    "fill": (165, 30, 42, 255),
    "outline": (22, 3, 5, 255),
    "facet": (92, 14, 22, 255),
    "text": (255, 255, 255, 255),
}

# --- Drawing the dice using pullow ---
def _palette(sides: int, value: int) -> dict:
    """
    Picks color scheme. Max is checked first, so it wins over 1. Just in case.
    """
    if value == sides:
        return GOLD
    if value == 1:
        return RED
    return PURPLE

def _polygon(n, cx, cy, r, rot=POLYGON_START_ANGLE):
    """
    Defines the shape of the die using given parameters (if it's a polygon)
    """
    return [
        (cx + r * math.cos(math.radians(rot + 360 * i / n)),
        cy + r * math.sin(math.radians(rot + 360 * i /n)))
        for i in range(n)
    ]

def _shape(sides: int, size: int):
    """
    Return ('rect', None) or ('poly', points) for the given die. For die_tile and _polygon respectively.
    """
    c = size / 2 
    r = size * DIE_RADIUS
    match sides:
        case 4: # D4, triangle.
            return "poly", _polygon(3, c, c + r * D4_DROP, r)
        case 6: # D6, rounded square.
            return "rect", None
        case 8: # D8, tall narrow kite.
            return "poly", [(c, c -r), (c + r * D8_WIDTH, c - r * D8_SHOULDER),
                            (c, c + r), (c - r * D8_WIDTH, c - r * D8_SHOULDER)]
        case 10: # D10, wide diamond.
            return "poly", [(c, c - r * D10_HEIGHT), (c + r , c), (c, c + r * D10_HEIGHT), (c - r, c)]
        case 12: # D12, pentagon.
            return "poly", _polygon(5, c, c + r * D12_DROP, r)
        case 20: # D20, hexagon
            return "poly", _polygon(6, c, c, r)
        case _:
            raise ValueError(sides)

def _center(pts):
    """
    Average of corner points of a polygon die. (for facets and the number).
    """
    return (sum(x for x, _ in pts) / len(pts), sum(y for _, y in pts) / len(pts))

def _facets(sides: int, pts):
    """
    Returns a list of lines (each a list of points) to draw facets inside a polygon die.
    For most die this means shrinking the outline toward the center and then connecting it to the outer points.
    """
    cx, cy = _center(pts)

    def scaled(f):
        return [(cx + (x -cx) * f, cy + (y - cy) * f) for x, y in pts]

    match sides:
        case 4:
            mids = [((pts[i][0] + pts[(i + 1) % 3][0]) / 2,
                      (pts[i][1] + pts[(i + 1) % 3][1]) / 2) for i in range(3)]
            return [mids + [mids[0]]]
        case 8:
            top, right, bottom, left = pts      # order matches _shape
            q = (cx, cy + (bottom[1] - cy) * D8_MEET_POINT)  # Facet meeting point.
            return [[top, q], [left, q], [right, q]]

        case 10:
            top, right, bottom, left = pts      # order matches _shape
            half_w = right[0] - cx              
            e1 = (cx - half_w * D10_EQUATOR_SPREAD, cy)        # 2 points on the equator
            e2 = (cx + half_w * D10_EQUATOR_SPREAD, cy)
            return [[left, right], [top, e1], [top, e2], [bottom, e1], [bottom, e2]]
        case 12:
            inner = scaled(D12_INNER_SCALE)
            lines = [inner + [inner[0]]]
            lines += [[a, b] for a, b in zip(inner, pts)]
            return lines
        case 20:
            # Classic d20: hardest part, inner triangle on evert other hexagon corner.
            # Each triangle corner connected to its own hexagon corner and the two neighbours.
            inner = scaled(D20_INNER_SCALE)
            tri = [inner[0], inner[2], inner [4]]
            lines = [tri + [tri[0]]]
            for k, t in enumerate(tri):
                for j in (2 * k - 1, 2 * k, 2 * k +1):
                    lines.append([t, pts[j % 6]])
            return lines
    return []

@lru_cache(maxsize=None)
def die_tile(sides: int, value: int) -> Image.Image:
    """
    Actually makes the dice images (including text) and caches them for reuse through lru_cache.
    """
    size = TILE * SS
    img = Image.new("RGBA", (size, size), TRANSPARENT)
    draw = ImageDraw.Draw(img)
    border = OUTLINE_WIDTH * SS
    facet_w = FACET_WIDTH * SS
    pal = _palette(sides, value)

    kind, pts = _shape(sides, size)
    if kind == "rect":
        m = size * D6_MARGIN
        draw.rounded_rectangle(
            (m, m, size - m, size - m), radius=size * D6_CORNER_RADIUS,
            fill=pal["fill"], outline=pal["outline"], width=border,
        )
        tx, ty = size / 2, size / 2
    else:
        draw.polygon(pts, fill=pal["fill"])
        for line in _facets(sides, pts):
            draw.line(line, fill=pal["facet"], width=facet_w, joint="curve")
        draw.line(pts + [pts[0]], fill=pal["outline"], width=border, joint="curve") # Outline on top of facet ends.
        tx, ty = _center(pts)

    # Font size: smaller for two digits and for busy centered dice (see FONT_SCALE).
    one_digit, two_digits = FONT_SCALE.get(sides, FONT_SCALE_DEFAULT)
    frac = two_digits if len(str(value)) > 1 else one_digit
    font = ImageFont.load_default(size=int(size * frac))

    # Stroke in ouline color keeps number readable in the case of overlap with facet lines.
    draw.text((tx, ty), str(value), font=font, fill=pal["text"], anchor="mm",
              stroke_width=TEXT_STROKE_WIDTH * SS, stroke_fill=pal["outline"])

    return img.resize((TILE, TILE), Image.LANCZOS)

def render_rolls(sides: int, rolls: list[int]) -> discord.File:
    """
    Renders the final image.
    """
    cols = min(len(rolls), GRID_COLS)
    rows = math.ceil(len(rolls) / cols)
    canvas = Image.new("RGBA", (cols * TILE, rows * TILE), TRANSPARENT)
    for i, value in enumerate(rolls):
        tile = die_tile(sides, value)
        canvas.paste(tile, ((i % cols) * TILE, (i // cols) * TILE), tile)

    buf = io.BytesIO() 
    canvas.save(buf, "PNG")
    buf.seek(0)
    return discord.File(buf, filename="roll.png")


@bot.event
async def on_ready():
    print(f"{bot.user} has successfully connected to the cord!")

def make_roll_command(sides: int):
    async def roll(ctx: discord.ApplicationContext):
        await ctx.respond(file=render_rolls(sides, [roll_die(sides)]))
    return roll

for sides in (4, 6, 8, 10, 12, 20):
    bot.slash_command(name=f"d{sides}", description=f"Roll a d{sides}")(make_roll_command(sides))

@bot.slash_command(name="multiroll", description="Roll a die of your choice but multi")
async def goBig(
    ctx: discord.ApplicationContext, 
    die: discord.Option(str, "die of your choice", choices=DICE), 
    amount: discord.Option(int, "How many times?", min_value=1, max_value=100)
):
    await ctx.defer()
    rolls, total = multiroll(die, amount)
    await ctx.respond(f"total: {total}", file=render_rolls(int(die[1:]), rolls))

bot.run(os.getenv('DISCORD_TOKEN'))
