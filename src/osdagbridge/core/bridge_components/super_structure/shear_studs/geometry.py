"""
Shear Stud Geometry
===================
Geometric constraint functions for headed shear studs used in
composite bridge construction.

References
----------
IRC 22:2015  – Clause 606.6 – Detailing of Shear Connectors
IS 3935:1966 – Specification for Composite Construction
"""


def min_stud_head_diameter(d_stud_mm: float) -> float:
    """
    Minimum required diameter of the stud head.

    Formula
    -------
        min_head_diameter = 1.5 × d_stud        [IRC 22:2015 – Cl. 606.6]

    Parameters
    ----------
    d_stud_mm : float
        Nominal shank diameter of the shear stud (mm).

    Returns
    -------
    float
        Minimum required stud head diameter (mm), rounded to 2 decimal places.

    Raises
    ------
    ValueError
        If ``d_stud_mm`` is not a positive number.
    """
    if d_stud_mm is None or d_stud_mm <= 0:
        raise ValueError("d_stud_mm must be a positive number.")

    min_head_d = 1.5 * d_stud_mm
    return round(min_head_d, 2)


def min_stud_head_height(d_stud_mm: float) -> float:
    """
    Minimum required height of the stud head.

    Formula
    -------
        min_head_height = 0.667 × d_stud         [IS 3935:1966]

    Parameters
    ----------
    d_stud_mm : float
        Nominal shank diameter of the shear stud (mm).

    Returns
    -------
    float
        Minimum required stud head height (mm), rounded to 2 decimal places.

    Raises
    ------
    ValueError
        If ``d_stud_mm`` is not a positive number.
    """
    if d_stud_mm is None or d_stud_mm <= 0:
        raise ValueError("d_stud_mm must be a positive number.")

    min_head_height = 0.667 * d_stud_mm
    return round(min_head_height, 2)
