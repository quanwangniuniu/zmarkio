from dataclasses import dataclass


@dataclass(frozen=True)
class ModuleSpec:
    key: str
    label: str
    default_visible: bool = True
    default_order: int = 0
    route_prefixes: tuple[str, ...] = ()


@dataclass(frozen=True)
class SurfaceTypeSpec:
    key: str
    label: str


@dataclass(frozen=True)
class SurfaceSpec:
    key: str
    label: str
    default_visible: bool = True
    default_order: int = 0
    route_prefixes: tuple[str, ...] = ()
    parent: str | None = None
