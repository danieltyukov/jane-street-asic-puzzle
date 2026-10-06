"""SKY130 GDS layer numbers used by the extractor.

Every SKY130 mask layer has a GDS (layer, datatype) pair. The routing stack is
li1 (local interconnect) followed by five metal layers, with one cut layer
between each neighbouring pair. Datatype 20 is the drawn shape, 16 marks a pin
shape and 5 holds the text label that names it.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Conductor:
    name: str
    drawing: tuple[int, int]
    pin: tuple[int, int]
    label: tuple[int, int]


@dataclass(frozen=True)
class Cut:
    name: str
    layer: tuple[int, int]
    below: str
    above: str


CONDUCTORS = [
    Conductor("li1", (67, 20), (67, 16), (67, 5)),
    Conductor("met1", (68, 20), (68, 16), (68, 5)),
    Conductor("met2", (69, 20), (69, 16), (69, 5)),
    Conductor("met3", (70, 20), (70, 16), (70, 5)),
    Conductor("met4", (71, 20), (71, 16), (71, 5)),
    Conductor("met5", (72, 20), (72, 16), (72, 5)),
]

CUTS = [
    Cut("mcon", (67, 44), "li1", "met1"),
    Cut("via1", (68, 44), "met1", "met2"),
    Cut("via2", (69, 44), "met2", "met3"),
    Cut("via3", (70, 44), "met3", "met4"),
    Cut("via4", (71, 44), "met4", "met5"),
]

# Gate poly and the contact from li1 down to it. Off by default: following
# poly is physically correct, but licon also lands on diffusion, so only the
# contacts that sit on poly are used (see extract.py).
POLY = Conductor("poly", (66, 20), (66, 16), (66, 5))
LICON = Cut("licon", (66, 44), "poly", "li1")
POLY_RESISTOR = (66, 15)  # marks poly used as a resistor (inside conb_1)

# Body-terminal labels inside standard cells. They sit on the well layers, not
# on routing, and are tied to the supply rails by tap cells.
NWELL_LABEL = (64, 5)
PWELL_LABEL = (64, 59)

# Outline of the die and of each placed cell.
DIE_AREA = (235, 4)
PR_BOUNDARY = (236, 0)

# A layer outside the SKY130 layer map. The puzzle uses it for decorations.
CUSTOM_MARKS = (200, 0)

LABEL_TO_CONDUCTOR = {c.label: c.name for c in CONDUCTORS}
