"""Analysis layer — chirp (FRF/Bode), spectral (noise/FFT), step response.

Each is a self-contained module operating on a decoded DataFrame + sample rate
and returning plain dicts. Ported from the skill's chirp_analysis.py /
spectral_analysis.py / step_response.py.
"""
from . import chirp, spectral, step

__all__ = ["chirp", "spectral", "step"]
