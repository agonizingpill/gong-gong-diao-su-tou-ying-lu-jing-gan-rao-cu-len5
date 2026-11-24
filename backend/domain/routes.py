from domain.sculpture_bands_api import router as sculpture_bands
from domain.sight_cells_api import router as sight_cells
from domain.shadow_plans_api import router as shadow_plans
from domain.light_handoffs_api import router as light_handoffs
from domain.route_sketches_api import router as route_sketches

routers = (sculpture_bands, sight_cells, shadow_plans, light_handoffs, route_sketches,)
