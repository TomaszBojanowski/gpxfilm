<p align="center">
  <img src="data/icons/hicolor/scalable/apps/io.github.tomaszbojanowski.Gpxfilm.svg" width="128" height="128" alt="">
</p>

# gpxfilm

Film z trasy GPX narysowanej na mapie.

gpxfilm czyta plik GPX z zegarka albo telefonu i robi z niego film MP4. Trasa rysuje się sama na mapie z cieniowaną rzeźbą terenu, na mapie topograficznej albo na zdjęciach lotniczych, z nazwami miejsc, profilem wysokości i Twoimi zdjęciami po drodze. Domyślnie film ma rozdzielczość 4K i 60 klatek na sekundę.

*English: [README.md](README.md)*

## Co potrafi

- Rysuje trasę na mapie okolicy, a dystans, suma podejść i czas rosną razem z nią.
- Podpisuje schroniska, przełęcze, szczyty i miejscowości przy trasie, biorąc nazwy z OpenStreetMap.
- Przy starcie i mecie podaje wysokość oraz nazwę schroniska, szczytu, przełęczy albo wsi, przy której leżą.
- Pokazuje profil wysokości, który wypełnia się razem z trasą.
- Zaczyna od najazdu z widoku całego kraju, z postojem nad okolicą, na którym pokazuje jej najważniejsze miejscowości i najwyższe szczyty, a kończy planszą z podsumowaniem.
- Zatrzymuje się na dłuższych postojach i pokazuje zdjęcia tam, gdzie zostały zrobione. Dopasowuje je po godzinie albo po pozycji GPS.
- Może kolorować trasę według nachylenia, prędkości, tętna albo wysokości i pokazywać liczniki na żywo.
- Ma cztery podkłady mapy i cztery style barw.
- Ma okno ustawień w przeglądarce, z podglądem i szybką wersją roboczą filmu.
- Czyta pliki GPX z różnych urządzeń i aplikacji.
- Mówi po polsku i po angielsku.

## Wymagania

- Python 3.10 lub nowszy
- [ffmpeg](https://ffmpeg.org/) dostępny w `PATH`
- [git](https://git-scm.com/), do instalacji prosto z GitHuba

Program jest używany na Linuksie i macOS. Na Windows nie był testowany.

Zdjęcia w formacie HEIC (np. z iPhone'a) wymagają dodatkowego pakietu `pillow-heif`: `pipx inject gpxfilm pillow-heif`. Bez niego program je pomija.

## Instalacja

Przez [pipx](https://pipx.pypa.io/):

```bash
pipx install git+https://github.com/TomaszBojanowski/gpxfilm
```

Albo z kopii repozytorium (sklonowanej przez git albo rozpakowanej z ZIP-a z GitHuba), w jej katalogu:

```bash
pipx install .
```

Na macOS ffmpeg i pipx instaluje Homebrew: `brew install ffmpeg pipx`.

pipx umieszcza polecenie `gpxfilm` w katalogu `~/.local/bin`. Jeśli terminal potem nie znajduje tego polecenia, uruchom raz `pipx ensurepath` i otwórz nowe okno terminala.

Późniejsza aktualizacja:

```bash
pipx upgrade gpxfilm
```

Numer wersji wynika z historii gita, więc każda opublikowana tu zmiana to nowa wersja; zainstalowaną wersję pokazuje `pipx list`. Jeśli program jest zainstalowany ze sklonowanego repozytorium, najpierw pobierz w nim zmiany: `git pull`.

## Szybki start

```bash
# Jedna klatka, żeby sprawdzić kadr i podpisy
gpxfilm trasa.gpx --frame-only podglad.png

# Szybka wersja robocza: 1280×720, 30 kl./s
gpxfilm trasa.gpx --draft -o szkic.mp4

# Pełny film: 4K, z intrem, outrem i zdjęciami lotniczymi
gpxfilm trasa.gpx -o film.mp4 --route-duration 40 --intro --outro --map aerial

# Ze zdjęciami z katalogu, licznikami i znacznikami kilometrów
gpxfilm trasa.gpx -o film.mp4 --photos ~/Zdjecia/wycieczka --counters --km-markers
```

Pełną listę opcji pokazuje `gpxfilm --help`. Nazwy opcji są angielskie, a ich opisy i nazwy argumentów (np. `--photos KATALOG`) są w pomocy po polsku, gdy system jest po polsku.

## Okno ustawień

```bash
gpxfilm --gui
```

Polecenie otwiera w przeglądarce lokalną stronę ze wszystkimi ustawieniami, podglądem ostatniej klatki, szybką wersją roboczą całego filmu i paskiem postępu. Strona rozmawia tylko z programem na Twoim komputerze.

## Podkłady mapy

| `--map` | Podkład |
| --- | --- |
| `terrain` | Cieniowana rzeźba terenu rysowana przez program z danych wysokościowych (domyślny) |
| `topo` | OpenTopoMap |
| `satellite` | Zdjęcia satelitarne Sentinel-2, rozdzielczość 10 m |
| `aerial` | Urzędowe zdjęcia lotnicze udostępniane jako otwarte dane |
| adres kafli lub WMS | Własne źródło, z `{z}/{x}/{y}` albo `{bbox}` |

Zdjęcia lotnicze są dostępne dla Polski, Niemiec, Austrii, Szwajcarii, Liechtensteinu, Francji, Hiszpanii, Portugalii, Włoch, Czech, Słowacji, Słowenii, Belgii, Holandii, Luksemburga, Estonii i Finlandii, a poza Europą dla Stanów Zjednoczonych, Japonii, Tajwanu, Hongkongu, Singapuru oraz części Kanady, Australii i Argentyny. W pozostałych miejscach program bierze zdjęcia satelitarne.

Te usługi należą do wielu różnych urzędów i co jakiś czas się zmieniają. Które z nich odpowiadają w tej chwili, sprawdza polecenie:

```bash
gpxfilm --check-maps
```

## Najczęściej używane opcje

| Opcja | Znaczenie |
| --- | --- |
| `--title "A\nB"` | Tytuł, każdy wiersz po `\n` |
| `--route-duration 40` | Ile sekund rysuje się trasa |
| `--intro`, `--outro` | Najazd z widoku kraju; plansza końcowa |
| `--intro-peaks 5`, `--intro-places 4`, `--intro-radius 20` | Ile najwyższych szczytów pokazuje intro nad okolicą i po dojeździe kamery, ile najważniejszych miejscowości (miast, miasteczek, wsi) pokazuje nad okolicą (0: żadnych) i w promieniu ilu km od startu szuka ich nad okolicą |
| `--intro-label-duration 3` | Ile sekund podpisy miejscowości i szczytów w intrze są w pełni widoczne, od pojawienia się ostatniego do początku gaśnięcia, przy postoju nad okolicą i po dojeździe kamery (od 0,5 do 20). Bez opcji ok. 1,3 s i 0,8 s |
| `--label-style strong`, `--label-size 1.0` | Wygląd podpisów na mapie: `plain` (zwykłe), `strong` (z ciemnym obrysem, domyślnie) albo `badges` (plakietki na ciemnym tle), i ich wielkość od 0,7 do 2,0. Większe podpisy i plakietki zajmują więcej miejsca, więc może się ich zmieścić mniej |
| `--photos KATALOG` | Zdjęcia pokazywane w miejscu, gdzie powstały |
| `--stop-minutes 5` | Postoje trwające co najmniej tyle minut |
| `--counters`, `--km-markers` | Liczniki na żywo; znaczniki kilometrów |
| `--distance 14.3`, `--ascent 929` | Dystans w km i suma podejść w m z zegarka: film skończy się na tych liczbach, a dystans będzie zaokrąglony do 0,1 km, jak każdy dystans na filmie. Bez nich dystans liczony ze śladu różni się zwykle od zegarka o ok. ±2% |
| `--color-by slope` | Kolor trasy według nachylenia (`slope`), prędkości (`speed`), tętna (`heart-rate`) albo wysokości (`elevation`) |
| `--map-style night` | Styl barw: `natural` (naturalny), `night` (nocny), `light` (jasny), `gray` (szary) |
| `--name-language it` | Preferowany język nazw miejsc |
| `--rename "STARA=NOWA"`, `--skip NAZWA` | Zmiana nazwy miejsca albo szczytu na filmie (STARA dokładnie tak, jak na filmie), także przy starcie, mecie i w intrze; pominięcie miejsc, których nazwa zawiera ten tekst (wielkość liter nie ma znaczenia). Obie opcje można podać wiele razy. W oknie ustawień wystarczy kliknąć nazwę na liście pod podglądem, żeby ją zmienić, albo ×, żeby ją pominąć |
| `--timezone Europe/Rome` | Strefa czasowa dla godzin |
| `--music PLIK` | Muzyka w tle |
| `--size 1920x1080`, `--fps 30` | Rozmiar kadru (tylko 16:9, np. 3840x2160, 1920x1080, 1280x720) i liczba klatek na sekundę |
| `--language en` | Język napisów na filmie: `pl` albo `en` |

Ustawienia, których używasz zawsze, można zapisać w `~/.config/gpxfilm.toml`, pod nazwami opcji bez kresek na początku:

```toml
map = "aerial"
stop-minutes = 10
km-markers = true
```

Plik ustawień działa od Pythona 3.11. Pliki ustawień z wcześniejszych wersji, z dawnymi polskimi nazwami opcji albo z `intro-peak-radius` i `intro-peak-duration`, nadal działają; program raz podpowiada, które nazwy zmienić.

## Język

gpxfilm mówi po polsku i po angielsku. Komunikaty, okno ustawień i napisy na filmie są w języku systemu: decyduje pierwsza ustawiona zmienna z `LANGUAGE`, `LC_ALL`, `LC_MESSAGES` i `LANG`, a na macOS, gdy żadna nie jest ustawiona, ustawienie systemu. Przy innych językach program mówi po angielsku. `--language pl` albo `--language en` ustala tylko język filmu, więc w polskim systemie można zrobić film z angielskimi napisami i odwrotnie.

Polskie teksty są w pliku tłumaczeń gettext `po/pl.po`, który program czyta wprost.

## Co idzie przez sieć

gpxfilm pobiera z publicznych serwerów dane wysokościowe, dane mapy oraz zdjęcia lotnicze albo satelitarne dla obszaru trasy. Za pierwszym razem pobiera też dwa kroje pisma z Google Fonts i granice państw z Natural Earth. Z opcją `--auto-title` pyta jeszcze usługę Nominatim z OpenStreetMap o nazwę miejsca w środku trasy. Wszystko trzyma w pamięci podręcznej, więc ponowne zrobienie tego samego filmu nie wymaga pobierania.

Plik GPX i Twoje zdjęcia nigdy nie są nigdzie wysyłane. Serwery widzą, o jaki obszar poprosił program; z opcją `--intro` pytania o szczyty i miejscowości wokół startu zawierają też samo położenie startu (`--intro-peaks 0 --intro-places 0` albo `--no-osm` je pomija). W każdym zapytaniu program przedstawia się nazwą, wersją i adresem projektu, np. `gpxfilm/0.1.0 (+https://github.com/TomaszBojanowski/gpxfilm)`.

Pamięć podręczna jest w `~/.cache/gpxfilm` (w `$XDG_CACHE_HOME/gpxfilm`, gdy ta zmienna jest ustawiona, a na macOS w `~/Library/Caches/gpxfilm`); inne miejsce ustawia zmienna `GPXFILM_CACHE_DIR`.

## Źródła danych

Każdy film ma w rogu podpis źródła użytego podkładu, a gdy pokazuje nazwy z OpenStreetMap, także podpis OpenStreetMap. Publikując film, zostaw ten podpis widoczny. Film z podkładem `topo` podlega licencji CC BY-SA, tak jak OpenTopoMap.

| Dane | Źródło |
| --- | --- |
| Wysokości | [Mapterhorn](https://mapterhorn.com/), z otwartych krajowych i światowych modeli terenu |
| Szlaki, wody, nazwy miejsc | © autorzy [OpenStreetMap](https://www.openstreetmap.org/copyright), przez Overpass, a z `--auto-title` także przez Nominatim |
| Mapa topograficzna | [OpenTopoMap](https://opentopomap.org/) (CC-BY-SA) |
| Zdjęcia satelitarne | Sentinel-2 cloudless, [EOX](https://s2maps.eu/), zawiera zmodyfikowane dane Copernicus Sentinel |
| Zdjęcia lotnicze | Instytucje publiczne poszczególnych krajów i regionów; podpis każdej jest na filmie |
| Granice państw | [Natural Earth](https://www.naturalearthdata.com/) (domena publiczna) |
| Kroje pisma | Big Shoulders Display i Figtree (SIL Open Font License) |

## Dla programistów

```bash
pip install -e '.[test]'
pytest -q
```

Testy porównują narysowane kadry z obrazami wzorcowymi i działają bez dostępu do sieci.

## W planach

Plany mogą się zmienić.

W następnym wydaniu:

- Aplikacja dla macOS z własnym oknem, otwierana dwuklikiem, bez Terminala i karty przeglądarki. Będzie do pobrania jako plik .dmg, bez podpisu Apple.
- Przebudowany silnik, który działa osobno od okna ustawień, więc okno nigdy się nie zawiesza. Każdą długą czynność da się od razu przerwać, a dowolną chwilę filmu da się narysować bez liczenia filmu od początku.

Później (kolejność nie jest ustalona):

- Natywna aplikacja dla Linuksa, zrobiona w GTK 4 i libadwaita, instalowana jako Flatpak.
- Film w pionie 9:16, na telefon.
- Okno ustawień dostępne z telefonu w sieci domowej, gdy da się zabezpieczyć dostęp do niego.
- Lista źródeł map w osobnym pliku, którą można zmienić własnym plikiem, i cotygodniowa automatyczna kontrola, która zgłasza niedziałające źródła.
- Przejazd kolejką albo autobusem przy zatrzymanym zapisie śladu nie będzie doliczany do dystansu.
- Podpis szczytu odsunięty od znaku i połączony z nim cienką linią, żeby większe napisy nie wypychały szczytów z mapy.
- Nazwa miejsca przy starcie i mecie także wtedy, gdy mieści się tylko z godziną w osobnym wierszu.

## Licencja

Copyright 2026 Tomasz Bojanowski

gpxfilm jest wolnym oprogramowaniem na licencji GNU General Public License w wersji 3 lub nowszej. Zobacz [LICENSE](LICENSE). Dane map i zdjęcia zachowują licencje swoich źródeł.

Dane testowe w `tests/data` zawierają pliki z innych projektów, na ich własnych licencjach: kafle terenu z Mapterhorn (licencje jego źródeł), granice państw z Natural Earth (domena publiczna) oraz kroje Big Shoulders Display i Figtree (SIL Open Font License 1.1). Szczegóły w [tests/data/SOURCES.md](tests/data/SOURCES.md).
