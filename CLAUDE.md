# L4D2 LLM Bot — kontekst projektu

## Cel
Bot survivora w Left 4 Dead 2 sterowany przez LLM, działający na własnym serwerze (dedyk lub listen server) z SourceModem.

## Architektura (dwie warstwy)
- **Warstwa szybka (deterministyczna):** celowanie, strzelanie i ruch po navmeshu robią wbudowane boty survivorów L4D2 (NextBot). LLM NIE steruje celowaniem: latencja 0.5–5 s jest za duża.
- **Warstwa wolna (LLM):** decyzje wysokopoziomowe co ~1–3 s, np. dokąd iść, leczenie, podnoszenie kolegów, zbieranie itemów, trzymanie się grupy, wycofanie. Opcjonalnie wiadomości na czacie (`say`).

## Komponenty
1. **Plugin SourceMod (SourcePawn)**
   - Zależności: left4dhooks, RIPExt (HTTP/JSON).
   - Timer co N sekund zbiera stan gry do zwięzłego JSONa: pozycje survivorów, HP, stany (incap/pinned/black-and-white), ekwipunek, pobliskie Special Infected, itemy w zasięgu, flow distance / postęp mapy.
   - Wysyła stan przez HTTP POST do lokalnego proxy i odbiera rozkaz.
   - Wykonuje rozkaz przez VScript `CommandABot()` (np. via `L4D2_ExecVScriptCode` z left4dhooks):
     `BOT_CMD_MOVE`, `BOT_CMD_ATTACK`, `BOT_CMD_RETREAT`, `BOT_CMD_RESET`.
   - Rozkazy mają timeout. Po timeoucie lub błędzie bot wraca do normalnego AI (`BOT_CMD_RESET`).
   - `CommandABot` bywa kapryśny (bot gubi rozkaz albo AI go nadpisuje w walce), więc trzeba ponawiać.

2. **Proxy w Pythonie**
   - Prosty serwer HTTP (np. FastAPI) odbiera stan i woła LLM (API albo lokalny model 7–14B).
   - Trzyma prompt systemowy, krótką historię decyzji i parsowanie/walidację odpowiedzi.
   - Model odpowiada TYLKO ustrukturyzowanym JSONem z białej listy akcji, np.:
     `{"action": "move", "target": "medkit_3"}`, `{"action": "revive", "target": "Ellis"}`, `{"action": "say", "text": "..."}`.
   - Nigdy nie przekazujemy surowych komend konsoli z wyjścia modelu.

## Ograniczenia / uwagi
- Stan gry zmienia się szybciej niż odpowiedź modelu, dlatego rozkazy są „intencjami” z timeoutem, a nie sterowaniem klatka po klatce.
- Koszt tokenów: decyzja co ~2 s na bota szybko się sumuje, więc stan musi być zwięzły. Rozważ lokalny model.
- Pełna kontrola fake clienta przez `OnPlayerRunCmd` jest możliwa, ale wymaga napisania własnej warstwy szybkiej. Na razie poza zakresem.

## Pierwsze kroki
1. Szkielet pluginu: timer, zbieranie stanu, POST przez RIPExt, wykonanie jednej akcji (`move`).
2. Proxy z mockiem LLM (zwraca stałe rozkazy), żeby przetestować pętlę end-to-end.
3. Podpięcie prawdziwego modelu i rozszerzenie białej listy akcji.

## Struktura repo
- `voice/` — komendy głosowe: Vosk (offline, gramatyka z fraz) → `ems/l4d2_voicecmd/commands.txt`. Tabele fraz w `voice/commands/*.toml`.
- `vscript/addon/` — dodatek VScript (mapspawn_addon) czytający ten plik przez `FileToString` i wykonujący komendy przez `SendToConsole` (tylko listen server). `vscript/build_vpk.py` pakuje go do VPK.
  Biała lista prefiksów (`vocalize `, `scripted_user_func l4b,`) jest w `bridge.py` i w `.nut` — zmieniać razem.
  Rozkazy wykonuje Left 4 Bots 2 (komendy: https://github.com/smilz0/Left4Bots/blob/main/COMMANDS.md), kwestie daje ion's Vocalizer.
- `plugin/scripting/l4d2_llm_bot.sp` — plugin SourceMod (stan → POST → `L4D2_CommandABot`).
- `proxy/l4d2_llm_proxy/` — FastAPI: `schema.py` (protokół), `validate.py` (biała lista), `prompt.py` (prompt + historia), `backends.py` (mock / modele), `app.py`.
- `proxy/tests/` — testy pytest + przykładowy stan `sample_state.json`.
- Protokół JSON opisany w README.md; zmiany w nim robimy jednocześnie w pluginie i w `schema.py`.
