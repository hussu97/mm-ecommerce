"""
Clients that speak somebody else's protocol.

A provider knows how to talk to Lalamove, noon Send, Slider, Stripe, Ziina,
Tabby, Tamara, GrubOps, Mapbox or APNs — their endpoints, their field names,
their idea of a status. It decides nothing. The `*_service` beside it in the
matching domain package decides *when* to call, what to do with a refusal, and
what the answer means for an order.

Keeping the two apart is what lets a courier rename a field without that rename
reaching the order lifecycle.
"""
