# DevAgent State Machine

## States
`RECEIVED -> PLANNING -> WAIT_APPROVAL -> RUNNING -> RUN_TESTS -> REVIEWING -> DONE|FAILED|CANCELLED`

## Events
- `approve`
- `reject`
- `stop`

## Guard Rules (MVP)
- `approve` ist nur in `WAIT_APPROVAL` gueltig.
- `reject` ist in `PLANNING` und `WAIT_APPROVAL` gueltig.
- `stop` ist in `RUNNING`, `RUN_TESTS`, `REVIEWING` gueltig.
- Ungueltige Events muessen als Audit-Log protokolliert und verworfen werden.

Referenz-Implementierung:
- `core/state_machine.py`
