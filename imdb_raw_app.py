"""
IMDb raw data
"""
import argparse
import csv
import json
import re
from pathlib import Path

KINDS = {'movie': 'movie', 'tvSeries': 'tv series', 'tvMiniSeries': 'tv mini series',
         'tvEpisode': 'episode', 'tvMovie': 'tv movie', 'short': 'short'}
ROLES = {'cast': 'cast', 'director': 'director', 'writer': 'writer',
         'producer': 'producer', 'editor': 'editor', 'composer': 'composer',
         'cinematographer': 'cinematographer', 'art_director': 'art direction',
         'art_department': 'art department', 'assistant_director': 'assistant director',
         'casting_director': 'casting director', 'casting_department': 'casting department',
         'costume_designer': 'costume designer', 'costume_department': 'costume department',
         'editorial_department': 'editorial department', 'location_management': 'location management',
         'make_up_department': 'make up', 'miscellaneous': 'miscellaneous crew',
         'production_designer': 'production design', 'production_manager': 'production manager',
         'set_decorator': 'set decoration', 'special_effects': 'special effects',
         'stunts': 'stunt performer', 'transportation_department': 'transportation department',
         'visual_effects': 'visual effects'}


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def dump(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def nonempty(value):
    return value is not None and value != '' and value != [] and value != {}


def persons(items):
    result = []
    for item in items or []:
        ident = item.get('imdbId', '')
        if item.get('name') and re.fullmatch(r'nm\d+', ident):
            result.append({'name': item['name'], '_imdb_id': ident[2:]})
    return result


def original_image_url(url):
    if not isinstance(url, str):
        return None
    return re.sub(r'\._[^/]+(?=\.[A-Za-z0-9]+$)', '', url)


def canonical_title(title):
    if not isinstance(title, str):
        return None
    for article in ('The ', 'An ', 'A '):
        if title.startswith(article) and len(title) > len(article):
            return title[len(article):] + ', ' + article.strip()
    return title


def canonical_name(name):
    if not isinstance(name, str):
        return None
    parts = name.strip().split()
    if len(parts) < 2 or ',' in name:
        return name.strip()
    suffixes = {'Jr.', 'Sr.', 'II', 'III', 'IV'}
    if parts[-1] in suffixes and len(parts) >= 3:
        return parts[-2] + ', ' + ' '.join(parts[:-2] + [parts[-1]])
    return parts[-1] + ', ' + ' '.join(parts[:-1])


def legacy_money(value):
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s+([A-Z]{3})\s*', value)
    if not match:
        return value.strip() or None
    amount, currency = match.groups()
    number = format(float(amount), ',.0f') if '.' in amount else format(int(amount), ',d')
    symbols = {'USD': '$', 'EUR': '€', 'GBP': '£'}
    return symbols.get(currency, currency + ' ') + number


def enrich_legacy_fields(candidate, source, episodes, notes):
    if source.get('imdbId', '').startswith('nm'):
        name = candidate.get('name')
        canonical = canonical_name(name)
        if canonical:
            candidate['canonical name'] = canonical
            candidate['long imdb canonical name'] = canonical
        full_image = original_image_url(source.get('image_url'))
        if full_image:
            candidate['full-size headshot'] = full_image
        notes.append('Canonical person names are derived from the displayed name; unusual naming conventions require review.')
        return

    title = candidate.get('original title') or candidate.get('title')
    canonical = canonical_title(title)
    if canonical:
        candidate['canonical title'] = canonical
        year = candidate.get('year')
        if year:
            quoted = '"' + canonical + '"' if candidate.get('kind') in ('tv series', 'tv mini series') else canonical
            candidate['long imdb canonical title'] = '%s (%s)' % (quoted, year)
    full_image = original_image_url(source.get('cover_url'))
    if full_image:
        candidate['full-size cover url'] = full_image
    if candidate.get('composer'):
        candidate['original music'] = candidate['composer']
    if candidate.get('kind'):
        candidate['alternative kind'] = candidate['kind']
    if candidate.get('plot'):
        candidate['plot outline'] = candidate['plot']

    box_office = {}
    budget = legacy_money(source.get('production_budget'))
    worldwide = legacy_money(source.get('worldwide_gross'))
    weekend = legacy_money(source.get('weekend_gross'))
    if budget:
        box_office['Budget'] = budget + ' (estimated)'
    if worldwide:
        box_office['Cumulative Worldwide Gross'] = worldwide
    if weekend:
        box_office['Opening Weekend Gross'] = weekend
    if box_office:
        candidate['box office'] = box_office

    series = source.get('info_series') or {}
    displayed = [int(x) for x in series.get('display_seasons', []) if str(x).isdigit() and int(x) > 0]
    if displayed:
        candidate['number of seasons'] = len(set(displayed))
    if episodes:
        from datetime import date
        aired = []
        for episode in episodes:
            value = episode.get('release_date')
            season = episode.get('season_number')
            try:
                if type(season) is int and season > 0 and value and date.fromisoformat(value) <= date.today():
                    aired.append(season)
            except ValueError:
                pass
        if aired:
            candidate['seasons'] = max(aired)
            notes.append('The legacy seasons value is the highest season with an episode dated on or before collection day; future listed seasons remain in number of seasons.')
    notes.append('Canonical titles, plot outline and alternative kind are derived from collected fields; their wording may differ from the retired crawler.')


def adapt(imdb_id, source, episodes=None):
    if not re.fullmatch(r'(tt|nm)\d+', imdb_id) or source.get('imdbId') != imdb_id:
        raise ValueError('IMDb ID does not match source JSON')
    out = {'imdbID': imdb_id[2:]}
    notes = []

    def copy(new, old, transform=lambda x: x):
        if nonempty(source.get(new)):
            out[old] = transform(source[new])

    if imdb_id.startswith('nm'):
        for new, old in [('name', 'name'), ('birth_date', 'birth date'),
                         ('birth_place', 'birth place'), ('image_url', 'headshot')]:
            copy(new, old)
        copy('bio', 'mini biography', lambda x: [x])
        copy('height', 'height')
        copy('name', 'long imdb name')
        notes.append('Height is the metric source string. Personal quotes, trivia and aliases are not available in the basic person response.')
        return out, notes

    for key in ['title', 'year', 'plot', 'genres', 'countries']:
        copy(key, key)
    for new, old in [('cover_url', 'cover url'), ('languages_text', 'languages'),
                     ('sound_mixes', 'sound mix'), ('colorations', 'color info'),
                     ('title_localized', 'localized title')]:
        copy(new, old)
    ratios = source.get('aspect_ratios') or []
    if ratios and isinstance(ratios[0], list) and ratios[0] and ratios[0][0]:
        out['aspect ratio'] = ratios[0][0]
        if len(ratios[0]) > 1 and ratios[0][1]:
            out['aspect ratio'] += ' (' + ratios[0][1] + ')'
        notes.append('Aspect ratio uses the first returned version; all versions remain in the source response.')
    for new, old in [('country_codes', 'country codes'), ('languages', 'language codes')]:
        copy(new, old, lambda values: [x.lower() for x in values])
    kind = KINDS.get(source.get('kind'))
    if kind:
        out['kind'] = kind
    else:
        notes.append('Unmapped title kind: ' + str(source.get('kind')))
    if source.get('duration') and source['duration'] > 0:
        out['runtimes'] = [format(source['duration'], 'g')]
    if source.get('votes', 0) and source['votes'] > 0:
        out['votes'] = source['votes']
        if source.get('rating') and 0 < source['rating'] <= 10:
            out['rating'] = source['rating']
    categories = source.get('categories') or {}
    for new, old in ROLES.items():
        values = persons(categories.get(new))
        if values:
            out[old] = values
    if 'director' not in out:
        values = persons(source.get('directors'))
        if values:
            out['director'] = values
    stars = [x['name'] for x in source.get('stars', []) if x.get('name')]
    if stars:
        out['stars'] = stars
    companies = source.get('company_credits') or {}
    for new, old in [('production', 'production companies'), ('distribution', 'distributors'),
                     ('miscellaneous', 'other companies')]:
        values = []
        for company in companies.get(new, []):
            if not company.get('name'):
                continue
            value = {'name': company['name']}
            ident = company.get('imdbId', '')
            if old == 'other companies' and re.fullmatch(r'co\d+', ident):
                value['_imdb_id'] = ident[2:]
            values.append(value)
        if values:
            out[old] = values
    if episodes is not None:
        nested, seen = {}, set()
        for episode in episodes:
            season, number = episode.get('season_number'), episode.get('episode_number')
            ident = episode.get('imdbId', '')
            if (type(season) is not int or season < 0 or type(number) is not int or number < 1
                    or not re.fullmatch(r'tt\d+', ident)):
                notes.append('Skipped episode with unknown season/number/ID: ' + ident)
                continue
            group = nested.setdefault(str(season), {})
            if str(number) in group or ident in seen:
                raise ValueError('Duplicate episode ID or season/episode slot: ' + ident)
            seen.add(ident)
            value = {'kind': 'episode', '_imdb_id': ident[2:], 'season': season, 'episode': number}
            for key in ['title', 'year', 'genres']:
                if nonempty(episode.get(key)):
                    value[key] = episode[key]
            if episode.get('votes', 0) and episode['votes'] > 0:
                value['votes'] = episode['votes']
                if episode.get('rating', 0):
                    value['rating'] = episode['rating']
            if episode.get('duration', 0) and episode['duration'] > 0:
                value['runtimes'] = [format(episode['duration'] / 60, 'g')]
            if episode.get('release_date'):
                value['original air date'] = episode['release_date']
            group[str(number)] = value
        if nested:
            out['episodes'] = nested
        notes.append('Episode dates preserve source values; January 1 may be a placeholder. Episode totals come from the API when available.')
    notes.append('Genres and runtime come from the basic response; regional variants are not verified.')
    
    return out, notes


from collections import defaultdict
FIELDS = {
    'credits': 'category { id } name { id nameText { text } }',
    'akas': 'text country { text } attributes { text }',
    'certificates': 'rating country { text } attributes { text }',
    'companyCredits': 'category { id } company { id companyText { text } }',
    # This group is experimental until tested against the live schema.
    'releaseDates': 'year month day country { text } attributes { text }',
}


def merge_extra(candidate, extra, roles):
    """Apply only groups whose traversal succeeded; report unrecognized categories."""
    notes = []
    results = extra.get('results', {})
    status = extra.get('status', {})
    role_map = dict(roles, actor='cast', actress='cast', self='cast')
    for group in FIELDS:
        if not status.get(group, {}).get('complete_pagination'):
            continue
        nodes = results.get(group, [])
        patch = {}
        try:
            if group == 'credits':
                buckets, seen, unknown = defaultdict(list), set(), set()
                for node in nodes:
                    category = node['category']['id']
                    role = role_map.get(category)
                    if not role:
                        unknown.add(category)
                        continue
                    person = node['name']; ident = person['id']
                    if not ident.startswith('nm') or not ident[2:].isdigit():
                        raise ValueError('Invalid person ID')
                    key = role, ident
                    if key not in seen:
                        seen.add(key)
                        buckets[role].append({'name': person['nameText']['text'], '_imdb_id': ident[2:]})
                patch.update(buckets)
                if unknown:
                    notes.append('Unmapped credit categories: ' + ', '.join(sorted(unknown)))
            elif group == 'akas':
                raw, formatted = [], []
                for node in nodes:
                    title = node['text']; country = (node.get('country') or {}).get('text')
                    if not country:
                        notes.append('AKA with no country omitted from legacy mapping: ' + title)
                        continue
                    raw.append({'title': title, 'countries': country})
                    suffix = ' '.join(x['text'] for x in node.get('attributes', []) if x.get('text'))
                    formatted.append(title + ' (' + country + ')' + (' ' + suffix if suffix else ''))
                if raw:
                    patch.update({'raw akas': raw, 'akas': formatted, 'akas from release info': formatted})
            elif group == 'certificates':
                values = []
                for node in nodes:
                    country = (node.get('country') or {}).get('text')
                    if not country or not node.get('rating'):
                        raise ValueError('Certificate missing country or rating')
                    text = country + ':' + node['rating']
                    for attr in node.get('attributes', []):
                        text += '::(' + attr['text'].strip('()') + ')'
                    values.append(text)
                if values:
                    patch['certificates'] = values
            elif group == 'companyCredits':
                keys = {'production': 'production companies', 'distribution': 'distributors', 'miscellaneous': 'other companies'}
                buckets = defaultdict(list)
                for node in nodes:
                    key = keys.get(node['category']['id'])
                    if key:
                        company = node['company']; value = {'name': company['companyText']['text']}
                        if key == 'other companies':
                            ident = company['id']
                            if not ident.startswith('co') or not ident[2:].isdigit():
                                raise ValueError('Invalid company ID')
                            value['_imdb_id'] = ident[2:]
                        if value not in buckets[key]:
                            buckets[key].append(value)
                patch.update(buckets)
            elif group == 'releaseDates':
                months = ['', 'January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']
                raw, formatted = [], []
                for node in nodes:
                    country = (node.get('country') or {}).get('text')
                    year, month, day = node.get('year'), node.get('month'), node.get('day')
                    if not country or not year:
                        raise ValueError('Release date missing country or year')
                    if not month or not day:
                        notes.append('Partial release date retained in graphql_extra.json only: ' + str(node))
                        continue
                    from datetime import date
                    date(year, month, day)
                    attrs = [a['text'] for a in node.get('attributes', [])]
                    item = {'date': '%s %s %s' % (day, months[month], year), 'country': country}
                    if attrs:
                        item['notes'] = '\n'.join(attrs)
                    raw.append(item)
                    text = '%s::%s %s, %s' % (country, months[month], day, year)
                    formatted.append(text + ''.join('::(' + a.strip('()') + ')' for a in attrs))
                if raw:
                    patch.update({'raw release dates': raw, 'release dates': formatted})
            candidate.update(patch)
        except (KeyError, TypeError, ValueError) as exc:
            notes.append('Group conversion failed; base values retained: %s: %s' % (group, exc))
    scalars = results.get('scalars', {})
    original = (scalars.get('originalTitleText') or {}).get('text')
    if original:
        candidate['original title'] = original
    years = scalars.get('releaseYear') or {}
    if candidate.get('kind') in ('tv series', 'tv mini series') and years.get('year'):
        candidate['series years'] = str(years['year']) + '-' + str(years.get('endYear') or '')
    total = ((results.get('series', {}).get('episodes') or {}).get('episodes') or {}).get('total')
    if type(total) is int and total >= 0:
        candidate['number of episodes'] = total
    return notes


def fetch_connection(imdb_id, group, request_fn, progress):
    categories = list(dict.fromkeys(['actor', 'actress', 'self'] + [k for k in ROLES if k != 'cast']))
    filter_text = ', filter: {categories: ' + json.dumps(categories) + '}' if group == 'credits' else ''
    query = ('query Pages($id: ID!, $after: ID) { title(id: $id) { '
             + group + '(first: 250, after: $after' + filter_text + ') {'
             + 'edges { node { ' + FIELDS[group] + ' } } pageInfo {endCursor hasNextPage} } } }')
    nodes, cursors, cursor = [], set(), None
    for page in range(1, 101):
        progress('%s - page %d' % (group, page))
        connection = request_fn(query, {'id': imdb_id, 'after': cursor})[group]
        info = connection['pageInfo']
        if type(info.get('hasNextPage')) is not bool:
            raise ValueError('Missing pageInfo.hasNextPage')
        edges = connection['edges']
        if not isinstance(edges, list):
            raise ValueError('Invalid edges list')
        nodes.extend(edge['node'] for edge in edges)
        if not info['hasNextPage']:
            return nodes, page
        cursor = info.get('endCursor')
        if not cursor or cursor in cursors:
            raise ValueError('Incomplete pagination: repeated or missing cursor')
        cursors.add(cursor)
    raise ValueError('100-page limit reached; group is incomplete')


def build_result(imdb_id, source, episodes, extra, steps, sources):
    raw, notes = adapt(imdb_id, source, episodes)
    before = len(notes)
    notes.extend(merge_extra(raw, extra, ROLES))
    enrich_legacy_fields(raw, source, episodes, notes)
    conversion_errors = [x for x in notes[before:] if x.startswith('Group conversion failed')]
    if conversion_errors:
        steps.append({'group': 'conversion', 'status': 'error', 'detail': '\n'.join(conversion_errors)})
    unresolved = (['birth name', 'nick names', 'quotes', 'trivia', 'trade mark',
                   'salary history']
                  if imdb_id.startswith('nm') else
                  ['production status', 'keywords (legacy keywords, not interests)'])
    for field in (('canonical name', 'full-size headshot') if imdb_id.startswith('nm') else
                  ('canonical title', 'long imdb canonical title', 'box office', 'full-size cover url')):
        if field not in raw:
            unresolved.append(field)
    if raw.get('kind') in ('tv series', 'tv mini series'):
        for field in ('seasons', 'number of seasons'):
            if field not in raw:
                unresolved.append(field)
    return {'imdb_id': imdb_id, 'raw_data': raw, 'steps': steps, 'notes': notes,
            'unresolved': unresolved, 'processor_verified': False, 'sources': sources,
            'sql_allowed': not any(x['status'] == 'error' for x in steps)}


def collect_result(imdb_id, progress=print):
    from importlib.metadata import version
    from imdbinfo import get_movie, get_name, get_all_episodes
    from imdbinfo.services import request_graphql_url, GRAPHQL_URL
    if version('imdbinfo') != '0.11.0':
        raise ValueError('Use the tested version: python -m pip install imdbinfo==0.11.0')
    steps, sources = [], {}
    progress('Basic data: ' + imdb_id)
    model = (get_name if imdb_id.startswith('nm') else get_movie)(imdb_id, locale='en')
    if model is None:
        raise ValueError('IMDb returned no title/person')
    source = model.model_dump(mode='json')
    if source.get('imdbId') != imdb_id:
        raise ValueError('IMDb returned a different ID')
    sources['basic'] = source
    steps.append({'group': 'basic', 'status': 'ok', 'detail': source.get('title', source.get('name', ''))})
    extra = {'results': {}, 'status': {}}
    episodes = None
    if imdb_id.startswith('tt'):
        def request(query, variables):
            data = request_graphql_url({'Content-Type': 'application/json', 'x-imdb-user-country': 'US'},
                                       imdb_id, {'query': query, 'variables': variables}, GRAPHQL_URL)
            if data.get('errors'):
                raise ValueError(json.dumps(data['errors']))
            title = (data.get('data') or {}).get('title')
            if not isinstance(title, dict):
                raise ValueError('Missing title in GraphQL response')
            return title
        for group in FIELDS:
            try:
                nodes, pages = fetch_connection(imdb_id, group, request, progress)
                extra['results'][group] = nodes
                extra['status'][group] = {'complete_pagination': True, 'pages': pages, 'nodes': len(nodes)}
                steps.append({'group': group, 'status': 'ok', 'detail': '%d records / %d pages' % (len(nodes), pages)})
            except Exception as exc:
                extra['status'][group] = {'complete_pagination': False, 'error': str(exc)}
                steps.append({'group': group, 'status': 'error', 'detail': str(exc)})
        try:
            progress('Original title and years')
            extra['results']['scalars'] = request('query S($id:ID!){title(id:$id){originalTitleText{text} releaseYear{year endYear}}}', {'id': imdb_id})
            steps.append({'group': 'scalars', 'status': 'ok', 'detail': 'Original title and years'})
        except Exception as exc:
            steps.append({'group': 'scalars', 'status': 'error', 'detail': str(exc)})
        if source.get('kind') in ('tvSeries', 'tvMiniSeries'):
            try:
                progress('Series episodes')
                episodes = [x.model_dump(mode='json') for x in get_all_episodes(imdb_id, locale='en')]
                sources['episodes'] = episodes
                total_data = request('query N($id:ID!){title(id:$id){episodes{episodes(first:1){total}}}}', {'id': imdb_id})
                extra['results']['series'] = total_data
                total = total_data['episodes']['episodes']['total']
                if len(episodes) != total:
                    raise ValueError('Received %d episodes; API reports %s' % (len(episodes), total))
                steps.append({'group': 'episodes', 'status': 'ok', 'detail': '%d episodes' % len(episodes)})
            except Exception as exc:
                steps.append({'group': 'episodes', 'status': 'error', 'detail': str(exc)})
    sources['graphql'] = extra
    result = build_result(imdb_id, source, episodes, extra, steps, sources)
    result['imdbinfo_version'] = version('imdbinfo')
    from datetime import datetime, timezone
    result['collected_at'] = datetime.now(timezone.utc).isoformat()
    return result


def sql_literal(text):
    if '\x00' in text:
        raise ValueError('NUL is not allowed in PostgreSQL text')
    return "E'" + text.replace('\\', '\\\\').replace("'", "''") + "'"


def insert_sql(imdb_id, raw):
    if not re.fullmatch(r'(tt|nm)\d+', imdb_id) or raw.get('imdbID') != imdb_id[2:]:
        raise ValueError('SQL ID does not match raw_data')
    def check(value):
        if isinstance(value, str):
            if '\x00' in value:
                raise ValueError('JSON contains a NUL character')
            value.encode('utf-8')
        elif isinstance(value, dict):
            for k, v in value.items():
                check(k); check(v)
        elif isinstance(value, list):
            for item in value:
                check(item)
    check(raw)
    payload = json.dumps(raw, ensure_ascii=False, allow_nan=False)
    return '''Run as a separate transaction in PostgreSQL.
New IMDb IDs only. Does not update existing rows or run processors.
inserted_rows=1: row inserted; 0: IMDb ID already exists.
BEGIN;
SET LOCAL lock_timeout = '5s';
LOCK TABLE public.imdb_raw_data IN SHARE ROW EXCLUSIVE MODE;
WITH inserted AS (
  INSERT INTO public.imdb_raw_data (imdb_id, raw_data, created_at, updated_at)
  SELECT %s, %s::jsonb, LOCALTIMESTAMP, LOCALTIMESTAMP
  WHERE NOT EXISTS (
    SELECT 1 FROM public.imdb_raw_data WHERE imdb_id = %s
  )
  RETURNING id, imdb_id
)
SELECT count(*) AS inserted_rows,
       coalesce(jsonb_agg(to_jsonb(inserted)), '[]'::jsonb) AS inserted_records
FROM inserted;
COMMIT;
''' % (sql_literal(imdb_id), sql_literal(payload), sql_literal(imdb_id))


def run_worker(imdb_id):
    import subprocess, sys, tempfile, time
    import streamlit as st
    with tempfile.TemporaryDirectory(prefix='imdb_app_') as directory:
        output = Path(directory) / 'result.json'
        log_path = Path(directory) / 'progress.log'
        progress = st.empty()
        with log_path.open('w', encoding='utf-8') as log:
            import os
            env = dict(os.environ, PYTHONIOENCODING='utf-8')
            process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--collect', imdb_id, str(output)],
                                       stdout=log, stderr=subprocess.STDOUT, env=env)
            started = time.monotonic()
            try:
                while process.poll() is None:
                    if time.monotonic() - started > 900:
                        raise TimeoutError('Collection exceeded 15 minutes and was stopped.')
                    lines = log_path.read_text(encoding='utf-8', errors='replace').splitlines()
                    if lines:
                        progress.info(lines[-1])
                    time.sleep(0.5)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
        progress.empty()
        if not output.exists():
            raise RuntimeError(log_path.read_text(encoding='utf-8', errors='replace')[-3000:])
        result = json.loads(output.read_text(encoding='utf-8'))
        if result.get('fatal_error'):
            raise RuntimeError(result['fatal_error'])
        return result


def parse_ids(text):
    tokens = [x for x in re.split(r'[\s,;]+', text.strip()) if x]
    invalid = [x for x in tokens if not re.fullmatch(r'(tt|nm)\d+', x)]
    if not tokens:
        raise ValueError('Enter at least one IMDb ID.')
    if invalid:
        raise ValueError('Invalid IMDb IDs: ' + ', '.join(invalid[:10]))
    return list(dict.fromkeys(tokens)), len(tokens) - len(set(tokens))


def execute_batch(ids, worker, notify=lambda *args: None):
    results = []
    for index, ident in enumerate(ids):
        notify(index, len(ids), ident)
        try:
            item = worker(ident)
            if item.get('imdb_id') != ident:
                raise ValueError('Returned IMDb ID does not match the requested ID')
            if item.get('sql_allowed'):
                insert_sql(ident, item['raw_data'])  # validate export before marking eligible
            results.append(item)
        except Exception as exc:
            results.append({'imdb_id': ident, 'fatal_error': str(exc), 'sql_allowed': False})
    notify(len(ids), len(ids), '')
    return results


def batch_exports(results):
    import io, zipfile
    buffer = io.BytesIO()
    statements, summary = [], []
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for result in results:
            ident = result['imdb_id']
            if not re.fullmatch(r'(tt|nm)\d+', ident):
                raise ValueError('Invalid export ID')
            raw = result.get('raw_data')
            eligible = bool(result.get('sql_allowed')) and raw is not None
            status = 'failed' if result.get('fatal_error') else ('collected' if eligible else 'incomplete')
            summary.append({'imdb_id': ident, 'status': status, 'included_in_sql': eligible,
                            'error': result.get('fatal_error', '')})
            archive.writestr(ident + '/report.json', json.dumps(result, ensure_ascii=False, indent=2))
            if raw is not None:
                archive.writestr(ident + '/raw_data.json', json.dumps(raw, ensure_ascii=False, indent=2))
            if eligible:
                sql = insert_sql(ident, raw)
                statements.append(sql)
                archive.writestr(ident + '/insert.sql', sql)
        combined = '-- One transaction per IMDb ID. Failed/incomplete collections are excluded.\n\n' + '\n'.join(statements)
        if statements:
            archive.writestr('all_inserts.sql', combined)
        archive.writestr('batch_summary.json', json.dumps(summary, ensure_ascii=False, indent=2))
    return buffer.getvalue(), combined if statements else '', summary


def render_result(result):
    import streamlit as st
    actual_id = result['imdb_id']
    if result.get('fatal_error'):
        st.error(actual_id + ': ' + result['fatal_error'])
        return
    raw = result['raw_data']
    st.subheader(raw.get('title', raw.get('name', actual_id)) + ' | ' + actual_id)
    if actual_id.startswith('tt'):
        columns = st.columns(4)
        for column, (key, label) in zip(columns, [('cast', 'Cast'), ('director', 'Directors'), ('producer', 'Producers'), ('writer', 'Writers')]):
            column.metric(label, len(raw.get(key, [])))
    else:
        st.caption('Person record: biography, birth details, height and image when available.')
    status_tab, raw_tab, sql_tab = st.tabs(['Collection status', 'Raw JSON', 'SQL INSERT'])
    with status_tab:
        st.dataframe(result['steps'], use_container_width=True, hide_index=True)
        st.warning('Processor compatibility is not yet verified. Successful collection does not guarantee complete legacy field coverage.')
        st.write('Known mapping gaps / review needed: ' + ', '.join(result['unresolved']))
        with st.expander('Detailed notes'):
            for note in result['notes']:
                st.write(note)
        st.download_button('Download full report and source responses', json.dumps(result, ensure_ascii=False, indent=2),
                           file_name=actual_id + '_report.json', mime='application/json', key='report_'+actual_id)
    with raw_tab:
        st.json(raw, expanded=False)
        st.download_button('Download raw JSON', json.dumps(raw, ensure_ascii=False, indent=2),
                           file_name=actual_id + '_raw.json', mime='application/json', key='raw_'+actual_id)
    with sql_tab:
        st.info('INSERT adds new IDs only. Existing IDs are skipped (inserted_rows=0). It does not update rows or run processors.')
        if not result['sql_allowed']:
            st.error('Collection or conversion failed for at least one group. SQL is excluded; inspect the report.')
        else:
            sql = insert_sql(actual_id, raw)
            st.code(sql, language='sql')
            st.download_button('Download SQL INSERT', sql, file_name=actual_id + '_insert.sql', mime='text/plain', key='sql_'+actual_id)


def app():
    import streamlit as st
    st.set_page_config(page_title='IMDb Raw Data Export', page_icon='🎬', layout='wide')
    st.title('IMDb Raw Data Export')
    st.caption('Collect movie, series and person data. Review raw JSON and export PostgreSQL INSERT statements. No database connection is used.')
    with st.form('fetch_batch'):
        text = st.text_area('IMDb IDs', value='tt9288030\ntt33764258\nnm0874339', height=150,
                            help='One ID per line. Spaces, commas and semicolons also work. You may mix movies, series and people.')
        submitted = st.form_submit_button('Collect data', type='primary')
    if submitted:
        for key in ['imdb_batch', 'imdb_batch_export', 'selected_imdb']:
            st.session_state.pop(key, None)
        try:
            ids, duplicate_count = parse_ids(text)
        except ValueError as exc:
            st.error(str(exc))
            return
        if duplicate_count:
            st.info('%d duplicate input(s) removed.' % duplicate_count)
        progress = st.progress(0.0)
        label = st.empty()
        def notify(done, total, ident):
            progress.progress(done / total)
            label.info(('Collecting %d of %d: %s' % (done + 1, total, ident)) if ident else 'Batch finished.')
        results = execute_batch(ids, run_worker, notify)
        st.session_state['imdb_batch'] = results
        st.session_state['imdb_batch_export'] = batch_exports(results)
    results = st.session_state.get('imdb_batch')
    if not results:
        st.info('IDs are processed sequentially. If one fails, the remaining IDs continue.')
        return
    if 'imdb_batch_export' not in st.session_state:
        st.session_state['imdb_batch_export'] = batch_exports(results)
    zip_data, combined_sql, summary = st.session_state['imdb_batch_export']
    st.subheader('Batch results')
    st.caption('Collected = requests/conversion succeeded; processor compatibility still requires a separate test.')
    st.dataframe(summary, use_container_width=True, hide_index=True)
    st.download_button('Download all results (ZIP)', zip_data, file_name='imdb_batch_results.zip', mime='application/zip')
    if combined_sql:
        st.download_button('Download combined SQL INSERTs', combined_sql, file_name='imdb_batch_inserts.sql', mime='text/plain')
    selected = st.selectbox('Inspect an IMDb ID', [r['imdb_id'] for r in results], key='selected_imdb')
    render_result(next(r for r in results if r['imdb_id'] == selected))


if __name__ == '__main__':
    import sys
    if len(sys.argv) == 4 and sys.argv[1] == '--collect':
        try:
            result = collect_result(sys.argv[2], lambda message: print(message, flush=True))
        except Exception as exc:
            result = {'fatal_error': type(exc).__name__ + ': ' + str(exc)}
        Path(sys.argv[3]).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    else:
        app()
