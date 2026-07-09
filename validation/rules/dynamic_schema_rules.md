# Dynamic Schema Rules

## Rule 1: Never rely on field order

JSON object order is not stable.

## Rule 2: Treat missing fields as expected

A missing field may mean the field is not applicable, not that the response is invalid.

## Rule 3: Log unknown fields

Unknown fields should be captured with:

- timestamp
- endpoint
- symbol
- quoteType
- marketState
- JSON path
- value sample

## Rule 4: Use overlays

Start with a base endpoint schema, then apply security-type and marketState overlays.
