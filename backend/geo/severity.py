"""
NiDa — Dynamic Severity Scoring

Combines satellite-derived Fire Radiative Power (FRP), detection
confidence, and live wind speed into a single composite severity score
per fire cluster, as described in the NiDa literature review (Section
2.4) and research proposal (Section 4.3).

    severity = w_frp * norm(FRP) + w_conf * confidence_factor + w_wind * norm(wind_speed)

Higher wind speed increases severity because it (a) drives faster,
more elongated fire spread (see ellipse.py) and (b) is associated with
more erratic, harder-to-predict fire behavior in the literature.
"""

from dataclasses import dataclass
from typing import Optional

# Weights sum to 1.0; documented as a design choice, not empirically fit
# (a direction for future work once historical outcome data is available).
W_FRP = 0.5
W_CONFIDENCE = 0.2
W_WIND = 0.3

# Normalization caps -- FRP and wind values above these are treated as
# maximum severity contribution (avoids a single extreme outlier point
# dominating the score).
FRP_CAP_MW = 200.0
WIND_CAP_KMH = 60.0


@dataclass
class SeverityResult:
    score: float           # 0.0 - 1.0
    level: str              # 'critical' / 'warning' / 'advisory'
    frp_component: float
    confidence_component: float
    wind_component: float


def compute_severity(
    max_frp_mw: float,
    has_high_confidence: bool,
    wind_speed_kmh: Optional[float],
) -> SeverityResult:
    """
    Compute a composite 0-1 severity score for a fire cluster.

    Args:
        max_frp_mw: peak Fire Radiative Power in the cluster (MW)
        has_high_confidence: True if any detection in the cluster was
            VIIRS/MODIS 'high' confidence
        wind_speed_kmh: current wind speed at the cluster location, or
            None if wind data was unavailable (contributes 0 in that case,
            documented as a conservative-by-omission design choice)

    Returns:
        SeverityResult with overall score, discrete level, and the
        individual weighted components (kept for transparency/debugging
        and for the paper's evaluation section).
    """
    frp_component = W_FRP * min(max(max_frp_mw, 0.0) / FRP_CAP_MW, 1.0)
    confidence_component = W_CONFIDENCE * (1.0 if has_high_confidence else 0.5)
    wind_component = (
        W_WIND * min(max(wind_speed_kmh, 0.0) / WIND_CAP_KMH, 1.0)
        if wind_speed_kmh is not None
        else 0.0
    )

    score = frp_component + confidence_component + wind_component
    score = min(score, 1.0)

    if score >= 0.65:
        level = "critical"
    elif score >= 0.35:
        level = "warning"
    else:
        level = "advisory"

    return SeverityResult(
        score=score,
        level=level,
        frp_component=frp_component,
        confidence_component=confidence_component,
        wind_component=wind_component,
    )
