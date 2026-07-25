"""
All SQLAlchemy models — imported here so Alembic can autodiscover them.
"""

from .base import Base  # noqa: F401 — must be first
from .user import User  # noqa: F401
from .address import Address, RegionEnum  # noqa: F401
from .category import Category  # noqa: F401
from .product import Product  # noqa: F401
from .modifier import Modifier, ModifierOption, ProductModifier  # noqa: F401
from .cart import Cart, CartItem  # noqa: F401
from .order import Order, OrderItem, OrderStatusEnum, DeliveryMethodEnum  # noqa: F401
from .promo_code import PromoCode, DiscountTypeEnum  # noqa: F401
from .refresh_token import RefreshToken  # noqa: F401
from .language import Language, UiTranslation  # noqa: F401
from .cms import CmsPage  # noqa: F401
from .blog import BlogPost  # noqa: F401
from .webhook_event import WebhookEvent  # noqa: F401
from .email_log import EmailLog  # noqa: F401
from .region import Region, DeliverySettings  # noqa: F401
from .audit_log import AuditLog  # noqa: F401
from .admin_passkey import AdminPasskey, WebAuthnChallenge  # noqa: F401

# ─── POS domain ───────────────────────────────────────────────────────────────
from .tax import Tax, TaxGroup, TaxGroupTax, TaxTypeEnum  # noqa: F401
from .branch import (  # noqa: F401
    Branch,
    BranchBusinessDay,
    BranchTypeEnum,
)
from .role import (  # noqa: F401
    ALL_PERMISSIONS,
    PERMISSION_DESCRIPTIONS,
    PERMISSION_GROUPS,
    Role,
    UserBranch,
)
from .payment_method import PaymentMethod, PaymentMethodTypeEnum  # noqa: F401
from .charge import Charge, ChargeTypeEnum  # noqa: F401
from .reason import Reason, ReasonTypeEnum  # noqa: F401
from .tag import Tag, TaggedEntity, TagTypeEnum  # noqa: F401
from .kitchen_flow import KitchenFlow, KitchenFlowCategory  # noqa: F401
from .device import (  # noqa: F401
    Device,
    DeviceStatusEnum,
    DeviceTypeEnum,
    Printer,
    PrinterConnectionEnum,
    PrinterRoleEnum,
)
from .pos_table import PosTable, Section, TableStatusEnum  # noqa: F401
from .till import (  # noqa: F401
    DRAWER_SIGN,
    DrawerOperation,
    DrawerOperationTypeEnum,
    Shift,
    Till,
    TillStatusEnum,
)
from .business_settings import (  # noqa: F401
    BusinessSettings,
    KitchenSortingEnum,
    ReceiptLanguageModeEnum,
)
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
from .menu import (  # noqa: F401
    Allergen,
    BranchProduct,
    Combo,
    ComboItem,
    ComboOption,
    ComboSize,
    MenuGroup,
    MenuGroupProduct,
    PriceTag,
    ProductAllergen,
    ProductPrice,
    SellingMethodEnum,
)
from .marketing import (  # noqa: F401
    Discount,
    DiscountQualificationEnum,
    GiftCard,
    GiftCardStatusEnum,
    GiftCardTransaction,
    GiftCardTransactionTypeEnum,
    HouseAccount,
    HouseAccountTransaction,
    HouseAccountTransactionTypeEnum,
    LoyaltyProgram,
    LoyaltyTransaction,
    LoyaltyTransactionTypeEnum,
    Promotion,
    PromotionRewardEnum,
    PromotionTriggerEnum,
    PromotionTypeEnum,
    TimedEvent,
    TimedEventTypeEnum,
)
from .operations import (  # noqa: F401
    NotificationRule,
    Reservation,
    ReservationStatusEnum,
    SpotCheck,
    SpotCheckItem,
    TransferOrder,
    TransferOrderItem,
    TransferOrderStatusEnum,
)
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

__all__ = [
    "Base",
    "User",
    "Address",
    "RegionEnum",
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
    "EmailLog",
    "Region",
    "DeliverySettings",
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
    "BranchTypeEnum",
    "Role",
    "UserBranch",
    "ALL_PERMISSIONS",
    "PERMISSION_GROUPS",
    "PERMISSION_DESCRIPTIONS",
    "PaymentMethod",
    "PaymentMethodTypeEnum",
    "Charge",
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
    "Shift",
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
    "PriceTag",
    "ProductPrice",
    "MenuGroup",
    "MenuGroupProduct",
    "Allergen",
    "ProductAllergen",
    "BranchProduct",
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
    "GiftCard",
    "GiftCardStatusEnum",
    "GiftCardTransaction",
    "GiftCardTransactionTypeEnum",
    "LoyaltyProgram",
    "LoyaltyTransaction",
    "LoyaltyTransactionTypeEnum",
    "HouseAccount",
    "HouseAccountTransaction",
    "HouseAccountTransactionTypeEnum",
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
