"""Turn the projections we already compute into concrete proposed actions.

The maths stays where it is - VORP, replacement level and the lineup optimiser
are all reused unchanged. This layer only answers "so what should I do?", and
its answers go to a human before they go to Sleeper.
"""
