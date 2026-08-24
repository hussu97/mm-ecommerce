"""The order itself: creating one, pricing it, and moving it through its states.

`order_lifecycle.transition()` is the only thing that may assign
`Order.status`, and it carries the consequences of doing so."""
