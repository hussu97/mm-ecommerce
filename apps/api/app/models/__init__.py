"""
All SQLAlchemy models — imported here so Alembic can autodiscover them.
"""

from .address import Address  # noqa: F401
from .admin_passkey import AdminPasskey, WebAuthnChallenge  # noqa: F401
from .audit_log import AuditLog  # noqa: F401
from .base import Base  # noqa: F401 — must be first
from .blog import BlogPost  # noqa: F401
from .branch import (  # noqa: F401
    Branch,
    BranchBusinessDay,
    BranchHoliday,
    BranchTypeEnum,
)
from .business_settings import (  # noqa: F401
    BusinessSettings,
    KitchenSortingEnum,
    ReceiptLanguageModeEnum,
)
from .cart import Cart, CartItem  # noqa: F401
from .category import Category  # noqa: F401
from .charge import Charge, ChargeTypeEnum  # noqa: F401
from .cms import CmsPage  # noqa: F401
from .courier import (  # noqa: F401
    Courier,
    UnbatchedPromiseEnum,
)

# ─── POS domain ───────────────────────────────────────────────────────────────
from .course import Course  # noqa: F401
from .custom_order import (  # noqa: F401
    OCCUPIES_SLOT,
    CustomOrder,
    CustomOrderBlackout,
    CustomOrderSourceEnum,
    CustomOrderStatusEnum,
)
from .delivery_batch import (  # noqa: F401
    DELIVERY_TIMEZONE,
    MAX_DROPS_PER_ORDER,
    BatchStatusEnum,
    DeliveryBatch,
    DeliveryBatchGroup,
    DeliveryBatchWindow,
)
from .delivery_polygon import (  # noqa: F401
    DeliveryPolygon,
    DeliveryPolygonVersion,
    FulfilmentProviderEnum,
)
from .delivery_settings import DeliverySettings  # noqa: F401
from .device import (  # noqa: F401
    Device,
    DeviceStatusEnum,
    DeviceTypeEnum,
    Printer,
    PrinterConnectionEnum,
    PrinterRoleEnum,
)
from .device_push_token import (  # noqa: F401
    DevicePushToken,
    PushPlatformEnum,
)
from .email_log import EmailLog  # noqa: F401
from .grubops import (  # noqa: F401
    GrubOpsItemMap,
    GrubOpsLocationMap,
    GrubOpsSyncState,
)
from .grubops_order import GrubOpsOrderMap  # noqa: F401
from .inventory import (  # noqa: F401
    TRANSACTION_SIGN,
    CostingMethodEnum,
    InventoryCategory,
    InventoryItem,
    InventoryItemIngredient,
    InventoryLevel,
    InventoryTransaction,
    InventoryTransactionItem,
    InventoryTransactionTypeEnum,
    ModifierOptionIngredient,
    ProductIngredient,
    PurchaseOrder,
    PurchaseOrderItem,
    PurchaseOrderStatusEnum,
    Supplier,
    SupplierItem,
    TransactionStatusEnum,
    Warehouse,
)
from .kitchen_flow import KitchenFlow, KitchenFlowCategory  # noqa: F401
from .language import Language, UiTranslation  # noqa: F401
from .marketing import (  # noqa: F401
    Discount,
    DiscountQualificationEnum,
    Promotion,
    PromotionRewardEnum,
    PromotionTriggerEnum,
    PromotionTypeEnum,
    TimedEvent,
    TimedEventTypeEnum,
)
from .menu import (  # noqa: F401
    Allergen,
    BranchModifierOption,
    BranchProduct,
    Combo,
    ComboItem,
    ComboOption,
    ComboSize,
    MenuGroup,
    MenuGroupProduct,
    ProductAllergen,
    SellingMethodEnum,
)
from .modifier import Modifier, ModifierOption, ProductModifier  # noqa: F401
from .operations import (  # noqa: F401
    NotificationRule,
    TransferOrder,
    TransferOrderItem,
    TransferOrderStatusEnum,
)
from .order import DeliveryMethodEnum, Order, OrderItem, OrderStatusEnum  # noqa: F401
from .order_delivery import (  # noqa: F401
    FAILED_COURIER_STATUSES,
    NOON_SEND_FAILED_STATUSES,
    NOON_SEND_TERMINAL_STATUSES,
    TERMINAL_COURIER_STATUSES,
    CourierStatusEnum,
    NoonSendStatusEnum,
    OrderDelivery,
    is_collected,
    is_failed,
    is_terminal,
)
from .order_driver import OrderDriver  # noqa: F401
from .order_status_event import (  # noqa: F401
    OrderStatusEvent,
    StatusActor,
    StatusSourceEnum,
    acting_as,
    pending_events,
)
from .payment_gateway import (  # noqa: F401
    PaymentGateway,
    PaymentGatewayEnum,
    PaymentMethodEnum,
)
from .payment_method import PaymentMethod, PaymentMethodTypeEnum  # noqa: F401
from .payment_transaction import (  # noqa: F401
    PaymentTransaction,
    PaymentTransactionStatusEnum,
)
from .phone_verification import PhoneVerification  # noqa: F401
from .pos_order import (  # noqa: F401
    DeliveryStatusEnum,
    DiscountSourceEnum,
    KitchenTicket,
    KitchenTicketItem,
    KitchenTicketStatusEnum,
    OrderCharge,
    OrderDiscount,
    OrderItemStatusEnum,
    OrderPayment,
    OrderSourceEnum,
    OrderTax,
    OrderTypeEnum,
    PosOrderStatusEnum,
)
from .pos_table import PosTable, Section, TableStatusEnum  # noqa: F401
from .product import Product  # noqa: F401
from .promo_code import DiscountTypeEnum, PromoCode  # noqa: F401
from .reason import Reason, ReasonTypeEnum  # noqa: F401
from .refresh_token import RefreshToken  # noqa: F401
from .role import (  # noqa: F401
    ALL_PERMISSIONS,
    PERMISSION_DESCRIPTIONS,
    PERMISSION_GROUPS,
    Role,
    UserBranch,
)
from .tag import Tag, TaggedEntity, TagTypeEnum  # noqa: F401
from .tax import Tax, TaxGroup, TaxGroupTax, TaxTypeEnum  # noqa: F401
from .till import (  # noqa: F401
    DRAWER_SIGN,
    DrawerOperation,
    DrawerOperationTypeEnum,
    Till,
    TillStatusEnum,
)
from .url_redirect import UrlRedirect  # noqa: F401
from .user import User  # noqa: F401
from .webhook_event import WebhookEvent  # noqa: F401
from .webhook_log import WebhookLog  # noqa: F401

__all__ = [
    "Base",
    "User",
    "Address",
    "Category",
    "Product",
    "Modifier",
    "ModifierOption",
    "ProductModifier",
    "Cart",
    "CartItem",
    "Order",
    "OrderItem",
    "OrderStatusEnum",
    "DeliveryMethodEnum",
    "PromoCode",
    "DiscountTypeEnum",
    "RefreshToken",
    "Language",
    "UiTranslation",
    "CmsPage",
    "BlogPost",
    "WebhookEvent",
    "WebhookLog",
    "EmailLog",
    "DeliveryPolygon",
    "DeliveryPolygonVersion",
    "DeliverySettings",
    "FulfilmentProviderEnum",
    "Courier",
    "UnbatchedPromiseEnum",
    "PaymentGateway",
    "PaymentGatewayEnum",
    "PaymentMethodEnum",
    "PaymentTransaction",
    "PaymentTransactionStatusEnum",
    "DeliveryBatch",
    "DeliveryBatchGroup",
    "DeliveryBatchWindow",
    "BatchStatusEnum",
    "OrderDelivery",
    "OrderStatusEvent",
    "StatusActor",
    "StatusSourceEnum",
    "acting_as",
    "pending_events",
    "PhoneVerification",
    "CourierStatusEnum",
    "DevicePushToken",
    "PushPlatformEnum",
    "NoonSendStatusEnum",
    "is_failed",
    "is_terminal",
    "AuditLog",
    "AdminPasskey",
    "WebAuthnChallenge",
    # POS domain
    "Tax",
    "TaxGroup",
    "TaxGroupTax",
    "TaxTypeEnum",
    "Branch",
    "BranchBusinessDay",
    "BranchHoliday",
    "BranchTypeEnum",
    "Role",
    "UserBranch",
    "ALL_PERMISSIONS",
    "PERMISSION_GROUPS",
    "PERMISSION_DESCRIPTIONS",
    "PaymentMethod",
    "PaymentMethodTypeEnum",
    "Charge",
    "Course",
    "ChargeTypeEnum",
    "Reason",
    "ReasonTypeEnum",
    "Tag",
    "TaggedEntity",
    "TagTypeEnum",
    "KitchenFlow",
    "KitchenFlowCategory",
    "Device",
    "DeviceTypeEnum",
    "DeviceStatusEnum",
    "Printer",
    "PrinterRoleEnum",
    "PrinterConnectionEnum",
    "Section",
    "PosTable",
    "TableStatusEnum",
    "Till",
    "TillStatusEnum",
    "DrawerOperation",
    "DrawerOperationTypeEnum",
    "DRAWER_SIGN",
    "BusinessSettings",
    "ReceiptLanguageModeEnum",
    "KitchenSortingEnum",
    "OrderPayment",
    "OrderCharge",
    "OrderDiscount",
    "OrderTax",
    "KitchenTicket",
    "KitchenTicketItem",
    "KitchenTicketStatusEnum",
    "OrderTypeEnum",
    "OrderSourceEnum",
    "PosOrderStatusEnum",
    "OrderItemStatusEnum",
    "DeliveryStatusEnum",
    "DiscountSourceEnum",
    # Custom cakes
    "CustomOrder",
    "CustomOrderBlackout",
    "CustomOrderSourceEnum",
    "CustomOrderStatusEnum",
    "OCCUPIES_SLOT",
    # Inventory
    "InventoryCategory",
    "Warehouse",
    "InventoryItem",
    "InventoryLevel",
    "Supplier",
    "SupplierItem",
    "InventoryTransaction",
    "InventoryTransactionItem",
    "InventoryTransactionTypeEnum",
    "TransactionStatusEnum",
    "TRANSACTION_SIGN",
    "CostingMethodEnum",
    "PurchaseOrder",
    "PurchaseOrderItem",
    "PurchaseOrderStatusEnum",
    "ProductIngredient",
    "ModifierOptionIngredient",
    "InventoryItemIngredient",
    # Menu
    "MenuGroup",
    "MenuGroupProduct",
    "Allergen",
    "ProductAllergen",
    "BranchProduct",
    "BranchModifierOption",
    "Combo",
    "ComboSize",
    "ComboItem",
    "ComboOption",
    "SellingMethodEnum",
    # Marketing
    "Discount",
    "DiscountQualificationEnum",
    "Promotion",
    "PromotionTypeEnum",
    "PromotionTriggerEnum",
    "PromotionRewardEnum",
    "TimedEvent",
    "TimedEventTypeEnum",
    # Operations
    "TransferOrder",
    "TransferOrderItem",
    "TransferOrderStatusEnum",
    "SpotCheck",
    "SpotCheckItem",
    "Reservation",
    "ReservationStatusEnum",
    "NotificationRule",
]
