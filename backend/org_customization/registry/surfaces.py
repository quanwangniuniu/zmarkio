from .base import SurfaceSpec, SurfaceTypeSpec

SURFACE_TYPES = (
    SurfaceTypeSpec(key="group", label="Group"),
    SurfaceTypeSpec(key="item", label="Item"),
    SurfaceTypeSpec(key="tab", label="Tab"),
    SurfaceTypeSpec(key="card", label="Card"),
    SurfaceTypeSpec(key="panel", label="Panel"),
    SurfaceTypeSpec(key="provider", label="Provider"),
    SurfaceTypeSpec(key="filter", label="Filter"),
    SurfaceTypeSpec(key="section", label="Section"),
)

SURFACES: tuple[SurfaceSpec, ...] = ()
