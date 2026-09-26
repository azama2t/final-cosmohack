"""Photo counter: find floating litter items in a close-range photo (camera at the water) and count them.

Separate module, NOT the Sentinel-2 task. Trained/evaluated on FML (Lazzerini et al., SEANOE doi:10.17882/106148,
CC BY 4.0): first-person photos from an unmanned surface vehicle, single class "garbage".
Items per km^2 are only meaningful when the imaged water area of the frame is known; drones/satellite are not validated.
"""
from .metrics import ap_at_iou, ap_from_matched, bootstrap_ci, count_metrics, match_all, match_image  # noqa: F401

SURVEY_NOTE = ("тип съёмки — камера у воды (надводный аппарат, вид от первого лица); "
               "для шт./км² нужна известная площадь кадра; дроны и спутник — не проверено")
