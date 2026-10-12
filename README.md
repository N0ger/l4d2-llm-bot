# L4D2 LLM Bot

Boty survivorów w Left 4 Dead 2 sterowane głosem i (docelowo) przez LLM.
Kontekst projektu jest w [CLAUDE.md](CLAUDE.md).

## Komendy głosowe (Left 4 Bots 2 + ion's Vocalizer)

Mówisz do mikrofonu, a boty wykonują rozkaz:

- „czekajcie” → Twoja postać mówi *„Wait here!”* (kwestia z Vocalizera), a Left 4 Bots 2 zamienia ją w rozkaz `bots wait`;
- „trener, czekaj tutaj” → komenda `coach wait here` idzie prosto do L4B2, bez kwestii głosowej.

```
mikrofon ─▶ voice/ (Vosk, offline)   ─▶  left4dead2/ems/l4d2_voicecmd/commands.txt
                                                        │  co 0.1 s
                                     vscript/ (dodatek) ◀┘  FileToString
                                        │  SendToConsole("vocalize PlayerWaitHere")
                                        ▼
                              ion's Vocalizer (kwestia) ─▶ Left 4 Bots 2 (rozkaz)
```

Rozpoznawanie działa lokalnie, z modelem Vosk ograniczonym do fraz z tabeli komend
(gramatyka), więc łapie tylko konkretne słowa. Program nie symuluje klawiatury,
więc działa też pod Waylandem. Dodatek wykonuje wyłącznie komendy zaczynające się
od `vocalize ` albo `scripted_user_func l4b,`.

**Działa tylko na serwerze lokalnym** (gra z botami / „listen server”), bo
`SendToConsole` wykonuje komendę w konsoli hosta. Na serwerze dedykowanym albo
cudzym serwerze nie zadziała. Left 4 Bots 2 też musi działać u hosta.

### Instalacja

1. Zasubskrybuj w Warsztacie **Left 4 Bots 2** (z Left 4 Lib i NavFixes) i **ion's Vocalizer**.
2. Zbuduj dodatek i skopiuj go do gry:
   ```sh
   python3 vscript/build_vpk.py
   cp vscript/dist/l4d2_voicecmd.vpk ".../steamapps/common/Left 4 Dead 2/left4dead2/addons/"
   ```
   Po wczytaniu mapy w konsoli (`~`) powinien pojawić się wpis `[voicecmd] listening for l4d2_voicecmd/commands.txt`.
3. Zainstaluj program (Python 3.11+). `sounddevice` potrzebuje biblioteki PortAudio;
   na Fedorze/Kinoite: `sudo dnf install portaudio` w toolboksie albo w obrazie systemu.
   ```sh
   cd voice
   python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
   ```
4. Uruchom go przed grą albo w trakcie:
   ```sh
   .venv/bin/python -m l4d2_voice              # polski; --lang en dla angielskiego
   ```
   Przy pierwszym uruchomieniu pobierze się mały model Vosk (~50 MB) do `~/.cache/vosk`.
   Folder gry wykrywa się sam (Steam natywny i Flatpak); w razie czego użyj
   `--game-dir ".../Left 4 Dead 2/left4dead2"`.

Przydatne opcje:

| opcja | opis |
|---|---|
| `--check` | pokazuje słowa z tabeli, których model nie zna (np. imiona botów); te słowa trzeba zmienić |
| `--dry-run` | tylko wypisuje komendy, nic nie wysyła do gry |
| `--text` | zamiast mikrofonu wpisujesz frazy z klawiatury (test bez mówienia) |
| `--list-devices`, `--device N` | wybór mikrofonu |
| `--min-confidence 0.6` | odrzuca niepewne rozpoznania |
| `--cooldown 1.5` | ta sama komenda nie powtórzy się szybciej niż co tyle sekund |
| `--commands plik.toml` | własna tabela komend |

### Tabela komend

Frazy są w `voice/commands/pl.toml` i `en.toml`:

- `[[command]]`: fraza bez imienia bota. Domyślnie wywołuje kwestię Vocalizera
  (`vocalize ...`); L4B2 tłumaczy ją według `ems/left4bots2/cfg/vocalizer.txt`.
- `[[bot_command]]`: imię bota + fraza, w dowolnej kolejności („trener czekaj” albo
  „czekaj trener”). Wysyła komendę L4B2 dla tego bota, np. `scripted_user_func l4b,{bot},heal,me`.
- `[bots]`: jak wymawiasz imiona. Są tam też polskie odpowiedniki, bo model polski
  może nie znać „Coach” czy „Rochelle”: Coach = „trener”, Francis = „franek”, Louis = „ludwik”.

Wybrane komendy:

| mówisz | co się dzieje |
|---|---|
| prowadźcie | `vocalize PlayerLeadOn` → boty prowadzą |
| czekajcie / stójcie | `vocalize PlayerWaitHere` → boty czekają |
| naprzód | `vocalize PlayerEmphaticGo` → idą tam, gdzie patrzysz |
| za mną | `vocalize PlayerFollowMe` → jeden bot idzie za Tobą |
| wszyscy za mną | `bots follow me` |
| do mnie | `bots come` |
| ulecz mnie | `vocalize AskForHealth2` → bot Cię leczy |
| uleczcie się / apteczki | `vocalize iMT_PlayerSuggestHealth` → boty się leczą |
| wiedźma | `vocalize PlayerWarnWitch` → bot zabija wiedźmę, na którą patrzysz |
| anuluj | `vocalize PlayerStayTogether` → boty anulują rozkazy |
| *bot* czekaj / czekaj tutaj / czekaj tam | `<bot> wait` / `wait here` / `wait there` |
| *bot* za mną, do mnie, prowadź, idź tam | `follow me`, `come`, `lead`, `goto` |
| *bot* lecz się, ulecz mnie, daj mi, zamiana | `heal`, `heal me`, `give`, `swap` |
| *bot* rzuć, tabletki, weź to, nieś to | `throw`, `tempheal`, `use`, `carry` |
| *bot* anuluj / stop | `cancel` |

Komendy z kwestiami Vocalizera mogą czasem nie zadziałać, np. w trakcie walki
postać nie wypowie kwestii i L4B2 jej nie wyłapie. Jeśli to przeszkadza, zmień
`run` na komendę L4B2 (`scripted_user_func l4b,bots,wait`).

## Sterowanie przez LLM

Celowanie, strzelanie i chodzenie zostają przy wbudowanym AI botów. LLM co ~2 s
wybiera *intencje*, np. „idź po apteczkę” albo „wróć do grupy”.

```
L4D2 server (SourceMod)                      proxy (Python)                 LLM
┌──────────────────────┐   POST /decide    ┌──────────────────┐         ┌────────┐
│ l4d2_llm_bot.smx     │ ── stan (JSON) ──▶│ prompt + historia│ ──────▶ │ mock / │
│  timer → BuildState  │                   │ parsowanie       │         │ model  │
│  CommandABot (MOVE)  │ ◀── rozkazy ───── │ biała lista akcji│ ◀────── │        │
└──────────────────────┘                   └──────────────────┘         └────────┘
```

### Stan

- [x] Szkielet pluginu: timer, zbieranie stanu, POST przez RIPExt, akcje `move`, `reset` i `say`
- [x] Proxy z mockiem LLM (reguły zamiast modelu), walidacja, historia decyzji, testy
- [ ] Podpięcie prawdziwego modelu (API albo lokalny 7–14B)
- [ ] Więcej akcji: `revive`, `heal`, `pickup`, `attack`, `retreat`

### Plugin SourceMod

Wymagania na serwerze: SourceMod 1.11+, [left4dhooks](https://github.com/SilvDev/Left4DHooks)
i [RIPExt](https://github.com/ErikMinekus/sm-ripext).

Kompilacja: skopiuj `plugin/scripting/l4d2_llm_bot.sp` do `addons/sourcemod/scripting/`,
dołóż include'y left4dhooks i RIPExt do `scripting/include/` i uruchom:

```sh
./spcomp l4d2_llm_bot.sp -o ../plugins/l4d2_llm_bot.smx
```

Przy pierwszym uruchomieniu powstanie config `cfg/sourcemod/l4d2_llm_bot.cfg`:

| cvar | domyślnie | opis |
|---|---|---|
| `llm_bot_enable` | `1` | włącza pętlę decyzji |
| `llm_bot_url` | `http://127.0.0.1:8787/decide` | adres proxy |
| `llm_bot_interval` | `2.0` | co ile sekund wysyłany jest stan |
| `llm_bot_reissue` | `1.0` | co ile sekund ponawiane są aktywne rozkazy (AI je nadpisuje) |
| `llm_bot_order_timeout` | `8.0` | domyślny i maksymalny czas życia rozkazu |
| `llm_bot_http_timeout` | `5` | timeout HTTP w sekundach |
| `llm_bot_item_radius` | `1500` | zasięg itemów w stanie (najbliższe 16) |
| `llm_bot_infected_radius` | `1500` | zasięg Special Infected i Witch w stanie |
| `llm_bot_abort_radius` | `250` | anuluj ruch, gdy SI jest tak blisko (`0` = nigdy) |
| `llm_bot_characters` | *(puste)* | sterowane postacie, np. `Ellis,Coach`; puste = wszystkie boty |
| `llm_bot_debug` | `0` | loguje wysyłane i odbierane JSON-y |

Komendy admina: `sm_llmbot_status` (aktywne rozkazy) i `sm_llmbot_reset`.

Rozkaz `move` wykonuje `L4D2_CommandABot(..., BOT_CMD_MOVE, pos)` z left4dhooks i
jest ponawiany co `llm_bot_reissue` s. Bot wraca do normalnego AI
(`BOT_CMD_RESET`), gdy:

- dojdzie do celu,
- minie timeout,
- cel zniknie (item podniesiony, survivor zginął),
- bot zostanie powalony albo złapany,
- obok pojawi się SI,
- proxy zwróci błąd albo nie odpowie.

Boty zastępujące idle gracza nie są sterowane.

### Proxy

```sh
cd proxy
python -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m l4d2_llm_proxy --backend mock      # nasłuchuje na 127.0.0.1:8787
.venv/bin/python -m pytest                              # testy
```

Ręczny test bez gry:

```sh
curl -s -X POST localhost:8787/decide -H 'Content-Type: application/json' \
     --data @proxy/tests/sample_state.json
```

Backend `mock` działa na regułach. Bot bez apteczki idzie po najbliższą (do 900
jednostek), a bot oddalony od graczy o ponad 700 jednostek wraca do grupy. Mock
odpowiada tekstem JSON, tak jak model, więc przechodzi przez to samo parsowanie
i walidację. Prawdziwy model podłącza się jako nowa klasa w `backends.py` z metodą
`complete(messages, state) -> str`.

### Protokół (v1)

Plugin → proxy, `POST /decide`:

```json
{
  "v": 1, "seq": 42, "time": 312, "map": "c2m1_highway",
  "survivors": [
    {"name": "Ellis", "player": "Ellis", "bot": true, "controlled": true, "alive": true,
     "hp": 40, "temp_hp": 12, "incap": false, "ledge": false, "black_white": false,
     "pinned_by": "", "pos": [1100, -250, 64], "flow": 30,
     "primary": "smg", "secondary": "pistol", "throwable": "molotov",
     "medkit": "", "pills": "", "order": ""}
  ],
  "infected": [{"id": "si_7", "type": "hunter", "pos": [1700, -900, 128], "dist": 780}],
  "items": [{"id": "medkit_212", "pos": [1400, -100, 64], "dist": 280},
            {"id": "weapon_411", "class": "rifle", "pos": [1500, -350, 64], "dist": 300}]
}
```

`name` to postać (Nick, Ellis…), `flow` to postęp mapy w %, `dist` to odległość
do najbliższego żywego survivora, a `order` to aktualny rozkaz bota.

Proxy → plugin:

```json
{"orders": [{"bot": "Ellis", "action": "move", "target": "medkit_212", "timeout": 8.0},
            {"bot": "Ellis", "action": "say", "text": "Biorę apteczkę"}],
 "latency_ms": 640}
```

| akcja | pola | znaczenie |
|---|---|---|
| `move` | `target` (id itemu albo imię survivora), `timeout` 1–20 s | idź do celu |
| `reset` | – | anuluj rozkaz, wróć do AI |
| `say` | `text` (≤120 znaków) | wiadomość na czacie (max 1 na 10 s gry) |

Walidacja odbywa się dwa razy, w proxy i w pluginie. Bot musi być sterowany i
żywy, cel musi istnieć w stanie, a każdy bot dostaje najwyżej jeden rozkaz ruchu
na turę. `say` trafia na czat przez `PrintToChatAll`, nigdy przez konsolę. Bot,
o którym odpowiedź nie wspomina, kontynuuje bieżący rozkaz do timeoutu.
