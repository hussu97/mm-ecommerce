"""
Register a POS terminal and its printers for a branch.

The catalogue importer brings across everything Foodics knows about, but a
terminal is hardware the shop owns, not data Foodics exports. This creates the
device row a cashier pairs against and the receipt/kitchen printers it prints
to, so a freshly imported branch can be taken live in one command.

    python scripts/foodics/provision_terminal.py --branch B001 \
        --device C01 --pairing-code DEMO2345 \
        --receipt-printer 192.168.1.50 --kitchen-printer 192.168.1.51

Re-running updates the same device and re-arms its pairing code.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from app.models import Branch, Device, Printer  # noqa: E402
from app.models.base import utcnow  # noqa: E402


async def provision(db, args) -> None:
    branch = (
        await db.execute(select(Branch).where(Branch.reference == args.branch))
    ).scalar_one_or_none()
    if branch is None:
        raise SystemExit(f"no branch with reference {args.branch!r}")

    device = (
        await db.execute(select(Device).where(Device.reference == args.device))
    ).scalar_one_or_none()
    if device is None:
        device = Device(reference=args.device, branch_id=branch.id)
        db.add(device)

    device.name = args.device_name or f"{branch.name} Terminal"
    device.type = "cashier"
    device.branch_id = branch.id
    # Re-arm rather than reuse: a device already paired must be released before
    # another iPad can claim it, and the code has to be unused to be claimable.
    device.status = "available"
    device.token_hash = None
    device.pairing_code = args.pairing_code.upper()
    device.pairing_code_expires_at = utcnow() + timedelta(days=args.code_days)

    for role, host, drawer in (
        ("receipt", args.receipt_printer, True),
        ("kitchen", args.kitchen_printer, False),
    ):
        if not host:
            continue
        name = f"{branch.name} {role.title()} Printer"
        printer = (
            (
                await db.execute(
                    select(Printer).where(
                        Printer.branch_id == branch.id, Printer.role == role
                    )
                )
            )
            .scalars()
            .first()
        )
        if printer is None:
            printer = Printer(branch_id=branch.id, role=role)
            db.add(printer)
        printer.name = name
        printer.connection = "lan"
        printer.ip_address = host
        printer.port = args.printer_port
        printer.characters_per_line = args.characters_per_line
        # CP864 is the Arabic code page; only worth switching on when the
        # printer actually supports it, or Arabic prints as mojibake.
        printer.codepage = "cp864" if args.arabic else "cp437"
        printer.supports_arabic = args.arabic
        printer.has_cash_drawer = drawer
        printer.is_default = True
        printer.is_active = True

    await db.flush()
    print(f"branch   {branch.name} ({branch.reference})")
    print(f"device   {device.reference}  pairing code {device.pairing_code}")
    for role, host in (
        ("receipt", args.receipt_printer),
        ("kitchen", args.kitchen_printer),
    ):
        if host:
            print(f"printer  {role:<8} {host}:{args.printer_port}")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--branch", required=True, help="branch reference, e.g. B001")
    parser.add_argument("--device", default="C01", help="device reference")
    parser.add_argument("--device-name")
    parser.add_argument("--pairing-code", required=True)
    parser.add_argument("--code-days", type=int, default=365)
    parser.add_argument("--receipt-printer", help="IP address")
    parser.add_argument("--kitchen-printer", help="IP address")
    parser.add_argument("--printer-port", type=int, default=9100)
    parser.add_argument("--characters-per-line", type=int, default=42)
    parser.add_argument("--arabic", action="store_true", help="use the CP864 code page")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    args = parser.parse_args()

    if not args.database_url:
        parser.error("set DATABASE_URL or pass --database-url")

    engine = create_async_engine(args.database_url)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        await provision(db, args)
        await db.commit()
    await engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
