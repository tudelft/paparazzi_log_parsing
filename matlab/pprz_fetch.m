function logs = pprz_fetch(ids, varargin)
%PPRZ_FETCH Load flights from the logbook server by their flight number (the number in /flight/<id>).
%   logs = pprz_fetch(1234)                  one flight
%   logs = pprz_fetch([1234 1240 1301])      several; see pprz_logbook for a table of the numbers
%   logs = pprz_fetch(ids, 'scope', s)       what to download, one file per log unless s is 'flight':
%       'selected' (default)  the given flights of each log (10 s before takeoff to 10 s after landing)
%       'all'                 every flight of those logs
%       'log'                 the whole log of each (also the time between flights)
%       'flight'              one file per flight number
%   logs = pprz_fetch(ids, 'force', true)    download again even when a current copy is cached
%   T = pprz_flights(logs); f = pprz_flight(logs, 'number', 1234)   ready to plot
%   Each flight has flights(k).logbook: the comment, crew and project as the logbook shows them, its recordings
%   (e.g. the GCS log next to the SD log, to fetch by its number) and the setting changes and messages of those
%   other recordings. Exports are cached in pprz_cache_dir and reused while they would be identical; a change of
%   the log, parser or logbook data fetches them again. Without a connection, or when the server fails (HTTP 5xx),
%   the cached copies are used with a warning.
%   Connect first with pprz_connect.
    opt = struct('force', false, 'scope', 'selected');
    for k = 1:2:numel(varargin)
        opt.(lower(varargin{k})) = varargin{k + 1};
    end
    scope = lower(char(opt.scope));
    if ~any(strcmp(scope, {'selected', 'all', 'log', 'flight'}))
        error('pprz_fetch:scope', 'scope must be ''selected'', ''all'', ''log'' or ''flight''');
    end
    ids = unique(double(ids(:)'), 'stable');
    cfg = pprz.connection();
    folder = fullfile(cfg.cache, 'mat', pprz.config('tag', cfg.server));
    if strcmp(scope, 'flight')
        files = arrayfun(@(id) fetch_flight(id, folder, opt.force), ids, 'UniformOutput', false);
    else
        files = fetch_logs(ids, scope, folder, opt.force);
    end
    logs = pprz_load(files{:});
end

function file = fetch_flight(id, folder, force)
    % The export of one flight ("This flight" on the flight page).
    old = dir(fullfile(folder, sprintf('flight_%d_*.mat', id)));
    path = sprintf('/api/flights/%d/matlab', id);
    try
        job = pprz.api('POST', path, struct('check', true));
    catch err
        if ~use_cache(err) || isempty(old)
            rethrow(err);
        end
        [~, newest] = max([old.datenum]);
        file = fullfile(folder, old(newest).name);
        warning('pprz_fetch:offline', 'Flight %d: using the cached copy (%s)', id, err.message);
        return
    end
    file = fullfile(folder, sprintf('flight_%d_%s.mat', id, job.id(1:16)));
    if isfile(file) && ~force
        return
    end
    if ~strcmp(job.status, 'ready')
        job = pprz.api('POST', path, struct('check', false));
    end
    download(job, file, sprintf('Flight %d', id));
    remove_others(old, file);
end

function files = fetch_logs(ids, scope, folder, force)
    % One export per log: the server groups the numbers by log.
    try
        reply = pprz.api('POST', '/api/flights/matlab', struct('flight_ids', numbers(ids), 'scope', scope, ...
            'check', true));
    catch err
        if ~use_cache(err)
            rethrow(err);
        end
        files = offline(ids, scope, folder, err);
        return
    end
    exports = as_array(reply.exports);
    files = cell(1, numel(exports));
    for k = 1:numel(exports)
        e = exports(k);
        remember(folder, e.flight_ids, e.log_id);
        prefix = sprintf('log_%d_%s_', e.log_id, scope);
        file = fullfile(folder, sprintf('%s%s.mat', prefix, e.id(1:16)));
        files{k} = file;
        if isfile(file) && ~force
            continue
        end
        label = sprintf('Log %d (flight %s)', e.log_id, num2str(double(e.flight_ids(:)')));
        if ~strcmp(e.status, 'ready')
            started = pprz.api('POST', '/api/flights/matlab', struct('flight_ids', numbers(e.flight_ids), ...
                'scope', scope, 'check', false));
            e = as_array(started.exports);
        end
        download(e(1), file, label);
        if ~strcmp(scope, 'selected')  % 'all' and 'log' are the same whichever numbers ask for them
            remove_others(dir(fullfile(folder, [prefix '*.mat'])), file);
        end
    end
end

function ok = use_cache(err)
    % The cached copies stand in when the server cannot be reached or fails (HTTP 5xx), not for other errors.
    ok = any(strcmp(err.identifier, {'pprz:offline', 'pprz:unavailable'}));
end

function remove_others(old, file)
    % Older exports of the same flight or log.
    for k = 1:numel(old)
        stale = fullfile(old(k).folder, old(k).name);
        if ~strcmp(stale, file)
            delete(stale);
        end
    end
end

function text = numbers(ids)
    % "1,3,4": a list the server reads also for a single number (jsonencode writes 5, not [5])
    text = strjoin(arrayfun(@(x) sprintf('%d', x), double(ids(:)'), 'UniformOutput', false), ',');
end

function e = as_array(e)
    if iscell(e)
        e = [e{:}];
    end
end

function download(job, file, label)
    last = -1;
    while ~strcmp(job.status, 'ready')
        if any(strcmp(job.status, {'failed', 'missing'}))
            error('pprz_fetch:failed', '%s: the export failed: %s', label, char(string(job.error)));
        end
        pct = round(100 * double(job.progress));
        if pct ~= last
            fprintf('%s: preparing the export on the server, %d%%\n', label, pct);
            last = pct;
        end
        pause(1);
        job = pprz.api('GET', sprintf('/api/matlab-exports/%s', job.id));
    end
    fprintf('%s: downloading %s\n', label, job.filename);
    folder = fileparts(file);
    if ~isfolder(folder)
        mkdir(folder);
    end
    cfg = pprz.connection();  % the key may be new: pprz.api signs in again when needed
    opts = weboptions('HeaderFields', {'Authorization', ['Bearer ' cfg.key]}, 'Timeout', 300);
    partial = [file '.part'];
    websave(partial, [cfg.server job.download], opts);
    movefile(partial, file, 'f');
end

function files = offline(ids, scope, folder, err)
    % Without a connection: the newest cached file of each log the numbers belong to.
    index = known(folder);
    logs = [];
    for id = ids
        hit = index(index(:, 1) == id, 2);
        if isempty(hit)
            rethrow(err);
        end
        logs(end + 1) = hit(end); %#ok<AGROW>
    end
    logs = unique(logs, 'stable');
    files = cell(1, numel(logs));
    for k = 1:numel(logs)
        old = dir(fullfile(folder, sprintf('log_%d_%s_*.mat', logs(k), scope)));
        if isempty(old)
            rethrow(err);
        end
        [~, newest] = max([old.datenum]);
        files{k} = fullfile(folder, old(newest).name);
        warning('pprz_fetch:offline', 'Log %d: using the cached copy (%s)', logs(k), err.message);
    end
end

function index = known(folder)
    % flight number -> log id of earlier downloads (flight_logs.txt in the cache), for offline use
    index = zeros(0, 2);
    name = fullfile(folder, 'flight_logs.txt');
    if isfile(name)
        fid = fopen(name, 'r');
        data = fscanf(fid, '%d %d', [2 Inf]);
        fclose(fid);
        index = data';
    end
end

function remember(folder, flight_ids, log_id)
    if ~isfolder(folder)
        mkdir(folder);
    end
    index = known(folder);
    flight_ids = double(flight_ids(:));
    index = [index(~ismember(index(:, 1), flight_ids), :); flight_ids, repmat(double(log_id), numel(flight_ids), 1)];
    fid = fopen(fullfile(folder, 'flight_logs.txt'), 'w');
    fprintf(fid, '%d %d\n', index');
    fclose(fid);
end
