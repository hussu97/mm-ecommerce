"""
Pydantic schemas for the POS foundation entities.

This was one 1,017-line module covering twelve unrelated domains — taxes,
branches, payment methods, charges, reasons, tags, staff, devices, kitchen
flows, tables, tills and settings. Its own banner comments marked the twelve
boundaries; they are modules now.

Everything is re-exported here, so `from app.schemas.pos import X` keeps
working for all six routers that use it. Import from the submodule when you
know which one you want; the barrel is for the routers that pull from several.
"""

from ._base import OrderTypeLiteral, ORMModel, Translations
from .branches import (
    BranchCreate,
    BranchHolidayBase,
    BranchHolidayCreate,
    BranchHolidayResponse,
    BranchHolidayUpdate,
    BranchResponse,
    BranchUpdate,
    BusinessDayResponse,
    WeeklyHoursResponse,
    WeeklyHoursUpdate,
    WeeklyShift,
)
from .business_settings import BusinessSettingsResponse, BusinessSettingsUpdate
from .charges import ChargeCreate, ChargeResponse, ChargeUpdate
from .devices import (
    DeviceCreate,
    DevicePairRequest,
    DevicePairResponse,
    DeviceResponse,
    DeviceSessionResponse,
    DeviceUpdate,
    PrinterCreate,
    PrinterResponse,
    PrinterUpdate,
)
from .kitchen_flows import KitchenFlowCreate, KitchenFlowResponse, KitchenFlowUpdate
from .payment_methods import (
    PaymentMethodCreate,
    PaymentMethodResponse,
    PaymentMethodUpdate,
)
from .reasons import ReasonCreate, ReasonResponse, ReasonUpdate
from .staff import (
    PermissionCatalogue,
    PermissionEntry,
    PinLoginRequest,
    PinLoginResponse,
    RoleCreate,
    RoleResponse,
    RoleUpdate,
    StaffCreate,
    StaffResponse,
    StaffUpdate,
)
from .tables import (
    SectionCreate,
    SectionResponse,
    SectionUpdate,
    TableCreate,
    TableResponse,
    TableUpdate,
)
from .tags import (
    CourseCreate,
    CourseResponse,
    CourseUpdate,
    TagAssignment,
    TagCreate,
    TagResponse,
    TagUpdate,
)
from .taxes import (
    TaxCreate,
    TaxGroupCreate,
    TaxGroupResponse,
    TaxGroupUpdate,
    TaxResponse,
    TaxUpdate,
)
from .tills import (
    DrawerOperationCreate,
    DrawerOperationResponse,
    TillCloseRequest,
    TillOpenRequest,
    TillReport,
    TillResponse,
)

__all__ = [
    "ORMModel",
    "OrderTypeLiteral",
    "Translations",
    "BranchCreate",
    "BranchHolidayBase",
    "BranchHolidayCreate",
    "BranchHolidayResponse",
    "BranchHolidayUpdate",
    "BranchResponse",
    "BranchUpdate",
    "BusinessDayResponse",
    "WeeklyShift",
    "WeeklyHoursUpdate",
    "WeeklyHoursResponse",
    "BusinessSettingsResponse",
    "BusinessSettingsUpdate",
    "ChargeCreate",
    "ChargeResponse",
    "ChargeUpdate",
    "CourseCreate",
    "CourseResponse",
    "CourseUpdate",
    "DeviceCreate",
    "DevicePairRequest",
    "DevicePairResponse",
    "DeviceResponse",
    "DeviceSessionResponse",
    "DeviceUpdate",
    "DrawerOperationCreate",
    "DrawerOperationResponse",
    "KitchenFlowCreate",
    "KitchenFlowResponse",
    "KitchenFlowUpdate",
    "PaymentMethodCreate",
    "PaymentMethodResponse",
    "PaymentMethodUpdate",
    "PermissionCatalogue",
    "PermissionEntry",
    "PinLoginRequest",
    "PinLoginResponse",
    "PrinterCreate",
    "PrinterResponse",
    "PrinterUpdate",
    "ReasonCreate",
    "ReasonResponse",
    "ReasonUpdate",
    "RoleCreate",
    "RoleResponse",
    "RoleUpdate",
    "SectionCreate",
    "SectionResponse",
    "SectionUpdate",
    "StaffCreate",
    "StaffResponse",
    "StaffUpdate",
    "TableCreate",
    "TableResponse",
    "TableUpdate",
    "TagAssignment",
    "TagCreate",
    "TagResponse",
    "TagUpdate",
    "TaxCreate",
    "TaxGroupCreate",
    "TaxGroupResponse",
    "TaxGroupUpdate",
    "TaxResponse",
    "TaxUpdate",
    "TillCloseRequest",
    "TillOpenRequest",
    "TillReport",
    "TillResponse",
]
