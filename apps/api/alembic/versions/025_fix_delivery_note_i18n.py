"""Fix delivery_time_note i18n: whatsapp -> email, prepared -> delivered

Revision ID: 025
Revises: 024
Create Date: 2026-04-14
"""

from alembic import op

revision = "025"
down_revision = "024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        UPDATE i18n_strings
        SET value = 'Orders placed before 12 PM are delivered the next day. We''ll send you an email confirmation once your order is packed.'
        WHERE namespace = 'checkout'
          AND key = 'delivery_time_note'
          AND language_code = 'en'
    """)

    op.execute("""
        UPDATE i18n_strings
        SET value = 'الطلبات قبل الساعة 12 ظهراً تُسلَّم في اليوم التالي. سنُرسل لك تأكيداً عبر البريد الإلكتروني حين يُعبَّأ طلبك.'
        WHERE namespace = 'checkout'
          AND key = 'delivery_time_note'
          AND language_code = 'ar'
    """)


def downgrade() -> None:
    op.execute("""
        UPDATE i18n_strings
        SET value = 'Orders placed before 12 PM are prepared the next day. We''ll send you a WhatsApp confirmation once your order is packed.'
        WHERE namespace = 'checkout'
          AND key = 'delivery_time_note'
          AND language_code = 'en'
    """)

    op.execute("""
        UPDATE i18n_strings
        SET value = 'الطلبات قبل الساعة 12 ظهراً تُحضَّر في اليوم التالي. سنُرسل لك تأكيداً عبر واتساب حين يُعبَّأ طلبك.'
        WHERE namespace = 'checkout'
          AND key = 'delivery_time_note'
          AND language_code = 'ar'
    """)
