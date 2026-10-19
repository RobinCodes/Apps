"""The looks: grades somebody has already dialled in.

A slider is only useful to somebody who already knows what they want. The
point of this file is the other case — you have a holiday video that looks
flat, or a photograph of a grey afternoon, and what you want is for it to
look like something. So each look here is a complete grade with a name, and
the sliders are what you reach for afterwards to take it too far or pull it
back.

They are written as the differences from neutral, so reading one tells you
what it actually does, and a look loaded by an older version of the app
simply ignores any control it has never heard of.

Two things were deliberately avoided. None of these is a simulation of a
named film stock — the stocks are measured things, and an approximation of
one by eye is worth less than an honest description of what the grade does.
And none of them touches `exposure`: a look that brightens the picture is a
look that is wrong for half the pictures it is applied to, so the strength of
each is in its colour and its contrast, and the exposure slider stays yours.

`CATEGORIES` is the order the editor lists them in, which runs from the ones
that leave a picture looking like itself to the ones that do not.
"""

from __future__ import annotations

from .grade import Grade

LOOKS: list[dict] = []


def _add(key, name, category, blurb, **values):
    LOOKS.append({"key": key, "name": name, "category": category,
                  "blurb": blurb, "values": values})


# ------------------------------------------------------------- the honest ----
# Nothing here changes what the picture is of, or what colour anything in it
# was. These are the ones to leave on.

_add("none", "Untouched", "Plain",
     "Every slider at neutral. The picture as the file has it.")

_add("enhance", "Enhance", "Plain",
     "What a good print shop would do without being asked: a little more "
     "contrast, a little more local contrast, a touch of colour.",
     contrast=0.14, filmic=0.18, clarity=0.22, saturation=0.10, blacks=-0.06)

_add("crisp", "Crisp", "Plain",
     "Detail only — no colour touched at all. For documents, screenshots and "
     "anything shot through glass.",
     clarity=0.30, sharpen=0.45, filmic=0.10)

_add("punch", "Punch", "Plain",
     "The same idea as Enhance, taken as far as it goes before the shadows "
     "start to block up.",
     contrast=0.26, filmic=0.30, clarity=0.38, saturation=0.26,
     blacks=-0.12, whites=0.10)

_add("flat", "Flat", "Plain",
     "Contrast and colour pulled back, so that everything in the frame is "
     "visible at once. Useful before grading something yourself.",
     contrast=-0.22, saturation=-0.18, blacks=0.14, highlights=-0.22,
     shadows=0.24)

_add("lowlight", "Low Light", "Plain",
     "For something shot in the dark: the shadows opened up, the colour cast "
     "pulled out of them, and no extra contrast to make the noise worse.",
     shadows=0.52, blacks=0.06, midtones=0.18, saturation=0.08,
     temperature=-0.10, clarity=-0.08)

# ------------------------------------------------------------ the cinematic ----
# Split toning, which is most of what a graded film looks like.

_add("cinema", "Cinema", "Cinematic",
     "Teal in the shadows, amber in the highlights, and an S-curve that does "
     "not clip. The look of nearly every film poster since about 2004.",
     filmic=0.34, contrast=0.08, saturation=-0.08, blacks=0.05,
     shadow_tint="#1E4C5A", shadow_tint_amount=0.38,
     highlight_tint="#FFB66B", highlight_tint_amount=0.30,
     halation=0.16)

_add("goldenhour", "Golden Hour", "Cinematic",
     "Warm, hazy and lit from the side, whatever the weather was.",
     temperature=0.26, filmic=0.18, highlights=0.10,
     highlight_tint="#FFC477", highlight_tint_amount=0.42,
     shadow_tint="#3A2A4A", shadow_tint_amount=0.18,
     bloom=0.26, halation=0.22, glow=0.14, blacks=0.06)

_add("moonlight", "Moonlight", "Cinematic",
     "Day for night: the exposure left alone, the colour taken to the blue "
     "end, and the highlights held back so nothing looks sunlit.",
     temperature=-0.46, tint=-0.06, highlights=-0.34, shadows=0.12,
     saturation=-0.22, filmic=0.22,
     shadow_tint="#16284E", shadow_tint_amount=0.44,
     highlight_tint="#9FC4E8", highlight_tint_amount=0.22,
     vignette=0.26)

_add("bleach", "Bleach Bypass", "Cinematic",
     "Silver left in the print: hard contrast, most of the colour gone, and "
     "a cold cast across the whole frame.",
     contrast=0.30, filmic=0.42, saturation=-0.62, clarity=0.34,
     temperature=-0.12, whites=0.16,
     wash="#8FA3B0", wash_amount=0.18, wash_mode="overlay")

_add("cyber", "Neon", "Cinematic",
     "Magenta and cyan at full strength, with the highlights spilling. For "
     "anything shot at night in a city.",
     saturation=0.44, filmic=0.30, blacks=0.08,
     shadow_tint="#1B2E8C", shadow_tint_amount=0.42,
     highlight_tint="#FF4FD8", highlight_tint_amount=0.34,
     bloom=0.46, bloom_threshold=0.62, halation=0.24, glow=0.10)

_add("sunwash", "Sun Wash", "Cinematic",
     "Overexposed on purpose, the way a holiday looks when you remember it "
     "rather than when you were there.",
     blacks=0.26, contrast=-0.10, filmic=0.14, temperature=0.22,
     wash="#FFD9A8", wash_amount=0.30, wash_mode="screen",
     bloom=0.34, glow=0.26, saturation=-0.10)

_add("thriller", "Cold Open", "Cinematic",
     "Desaturated, hard and slightly green — the colour of a procedural.",
     saturation=-0.40, tint=-0.14, filmic=0.38, contrast=0.12,
     shadow_tint="#203028", shadow_tint_amount=0.34,
     highlight_tint="#D8E0D4", highlight_tint_amount=0.18,
     clarity=0.26, vignette=0.22)

# ------------------------------------------------------------- the chemical ----
# Looks that come from something physical going wrong, or going right.

_add("faded", "Faded", "Film",
     "A print left in the sun: no true black left, the colour gone soft, and "
     "a warm stain over everything.",
     blacks=0.34, contrast=-0.14, saturation=-0.26, midtones=0.10,
     wash="#D8B48C", wash_amount=0.24, wash_mode="soft-light",
     grain=0.18)

_add("crossprocess", "Cross Process", "Film",
     "Developed in the wrong chemistry: the shadows go cyan, the highlights "
     "go yellow, and the contrast climbs.",
     contrast=0.24, filmic=0.30, saturation=0.22,
     shadow_tint="#006E7A", shadow_tint_amount=0.52,
     highlight_tint="#FFE86B", highlight_tint_amount=0.38,
     blacks=0.10, grain=0.14)

_add("technicolor", "Three Strip", "Film",
     "Saturation past the point of plausibility, with the primaries pulled "
     "apart — the way a 1950s musical looks.",
     saturation=0.72, contrast=0.22, filmic=0.26, clarity=0.18,
     whites=0.10, halation=0.18)

_add("vhs", "Tape", "Film",
     "Recorded off the television and watched twice: soft, green, and noisy.",
     saturation=-0.22, tint=-0.16, contrast=-0.16, blacks=0.22,
     clarity=-0.30, glow=0.30, grain=0.30, grain_size=2.2,
     wash="#9FD8B4", wash_amount=0.14, wash_mode="soft-light")

_add("dream", "Dream", "Film",
     "A soft-focus filter over the lens, which is a sheet of glass with "
     "grease on it and has never stopped working.",
     glow=0.56, bloom=0.30, bloom_threshold=0.54, blacks=0.18,
     contrast=-0.18, saturation=-0.08, clarity=-0.22,
     highlight_tint="#FFE8F0", highlight_tint_amount=0.20)

_add("grain", "Pushed", "Film",
     "Shot at the wrong speed and pushed in development: grain, contrast and "
     "nothing else.",
     grain=0.46, grain_size=1.6, contrast=0.22, filmic=0.24, clarity=0.20,
     saturation=-0.10)

# ----------------------------------------------------------- the monochrome ----

_add("mono", "Mono", "Monochrome",
     "Desaturated on the luminance weights the eye actually uses, which is "
     "not the same as setting the colour slider to zero and hoping.",
     monochrome=True, filmic=0.16, clarity=0.14)

_add("noir", "Noir", "Monochrome",
     "Black and white with the contrast taken most of the way to graphic: "
     "the shadows closed, the highlights held, the corners burned.",
     monochrome=True, contrast=0.30, filmic=0.46, clarity=0.30,
     blacks=-0.16, vignette=0.40, grain=0.22)

_add("selenium", "Selenium", "Monochrome",
     "A warm-toned silver print — the luminance driving a ramp to a cool "
     "brown rather than to white, which is what toning actually does.",
     monochrome=True, duotone="#F2E4D0", filmic=0.22, clarity=0.16,
     shadow_tint="#3A2E3E", shadow_tint_amount=0.26, grain=0.12)

_add("cyanotype", "Cyanotype", "Monochrome",
     "The oldest photographic process there is, and still the bluest.",
     monochrome=True, duotone="#BFE2F2", contrast=0.10, filmic=0.20,
     shadow_tint="#0A2A4A", shadow_tint_amount=0.44, grain=0.16)

_add("infrared", "Infrared", "Monochrome",
     "Foliage white and skies black, by giving the red channel the weight "
     "the film gave it.",
     monochrome=True, hue=120, contrast=0.26, filmic=0.34,
     whites=0.14, clarity=0.24, grain=0.18)

CATEGORIES = ("Plain", "Cinematic", "Film", "Monochrome")


def by_key(key: str) -> dict | None:
    return next((look for look in LOOKS if look["key"] == key), None)


def grade_for(key: str) -> Grade:
    """A fresh grade carrying the named look, or a neutral one."""
    look = by_key(key)
    return Grade(look["values"]) if look else Grade()


def in_category(category: str) -> list[dict]:
    return [look for look in LOOKS if look["category"] == category]


def matching(values) -> str | None:
    """Which look a grade is, if it is exactly one of them and untouched since.

    The editor shows the name of the look that is selected, and showing one
    that the sliders have since been moved away from would be a lie — so the
    comparison is of every control, not of the ones the look happens to set.
    """
    probe = Grade(values)
    for look in LOOKS:
        if Grade(look["values"]) == probe:
            return look["key"]
    return None
