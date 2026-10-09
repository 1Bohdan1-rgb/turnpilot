"""G-code from the part zero (Z0 on the free end face, X0 on the axis) for the outer profile, and its simulation.

Pure modules, no Flask and no database: readiness (what may go into a program), profile (the part as r(z)),
program (a neutral list of commands), dialects (printing and reading back the text of one control), simulate
(the checks on the printed text). services.py builds their inputs from the database.
"""
