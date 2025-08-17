from fastapi import APIRouter
from ...observability.metrics import metrics_app

router = APIRouter(tags=["metrics"])
router.add_api_route("/metrics", metrics_app(), methods=["GET"])
