# Matrix Adapter (MVP)

Aufgaben:
- Room Listener fuer JobCard Events
- Reaction Mapping (`approve/reject/stop`)
- User-Allowlist und State-Guards

Implementiert in:
- `adapters/matrix/reactions.py` (Mapping + Guard-Entscheidung)
- `adapters/matrix/jobcard.py` (JobCard Event erzeugen/parsen)
- `adapters/matrix/listener.py` (Room/Sender-Filter + JobRequest-Extraction)
