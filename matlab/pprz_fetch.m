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
%   other recordings. Files are kept in the cache folder (pprz_server) and reused while the export would be
%   identical; a change of the log, parser or logbook data fetches it again. Without a connection the last
%   cached copy is used. Set the server once with pprz_server('https://logs.example.org', 'API-KEY').
    opt = struct('force', false, 'scope', 'selected');
    for k = 1:2:numel(varargin)
        opt.(lower(varargin{k})) = varargin{k + 1};
    end
    scope = lower(char(opt.scope));
    if ~any(strcmp(scope, {'selected', 'all', 'log', 'flight'}))
        error('pprz_fetch:scope', 'scope must be ''selected'', ''all'', ''log'' or ''flight''');
    end
    cfg = pprz_server();
    if ~isfolder(cfg.cache)
        mkdir(cfg.cache);
    end
    ids = unique(double(ids(:)'), 'stable');
    if strcmp(scope, 'flight')
        files = cell(1, numel(ids));
        for k = 1:numel(ids)
            files{k} = fetch_flight(ids(k), cfg, opt.force);
        end
    else
        files = fetch_logs(ids, scope, cfg, opt.force);
    end
    logs = pprz_load(files{:});
end

function file = fetch_flight(id, cfg, force)
    old = dir(fullfile(cfg.cache, sprintf('flight_%d_*.mat', id)));
    try
        job = pprz.api('POST', sprintf('/api/flights/%d/matlab', id), 'check', '1');
    catch err
        file = newest(cfg, old, sprintf('Flight %d', id), err);
        return
    end
    file = fullfile(cfg.cache, sprintf('flight_%d_%s.mat', id, job.id(1:16)));
    if isfile(file) && ~force
        return
    end
    if ~strcmp(job.status, 'ready')
        job = pprz.api('POST', sprintf('/api/flights/%d/matlab', id), 'check', '0');
    end
    download(job, file, cfg, sprintf('Flight %d', id));
    remove_others(cfg, old, file);
end

function files = fetch_logs(ids, scope, cfg, force)
    % One export per log: the server groups the numbers by log.
    list = strjoin(arrayfun(@(x) sprintf('%d', x), ids, 'UniformOutput', false), ',');
    try
        reply = pprz.api('POST', '/api/flights/matlab', 'flight_ids', list, 'scope', scope, 'check', '1');
    catch err
        files = offline(ids, scope, cfg, err);
        return
    end
    exports = reply.exports;
    if iscell(exports)
        exports = [exports{:}];
    end
    files = cell(1, numel(exports));
    for k = 1:numel(exports)
        e = exports(k);
        remember(cfg, e.flight_ids, e.log_id);
        prefix = sprintf('log_%d_%s_', e.log_id, scope);
        file = fullfile(cfg.cache, sprintf('%s%s.mat', prefix, e.id(1:16)));
        files{k} = file;
        if isfile(file) && ~force
            continue
        end
        label = sprintf('Log %d (flight %s)', e.log_id, num2str(e.flight_ids(:)'));
        if ~strcmp(e.status, 'ready')
            started = pprz.api('POST', '/api/flights/matlab', 'flight_ids', ...
                strjoin(arrayfun(@(x) sprintf('%d', x), e.flight_ids(:)', 'UniformOutput', false), ','), ...
                'scope', scope, 'check', '0');
            e = started.exports;
            if iscell(e)
                e = e{1};
            end
        end
        download(e, file, cfg, label);
        if ~strcmp(scope, 'selected')  % 'all' and 'log' are the same whichever numbers ask for them
            remove_others(cfg, dir(fullfile(cfg.cache, [prefix '*.mat'])), file);
        end
    end
end

function download(job, file, cfg, label)
    last = -1;
    while ~strcmp(job.status, 'ready')
        if strcmp(job.status, 'failed')
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
    opts = weboptions('HeaderFields', {'Authorization', ['Bearer ' cfg.key]}, 'Timeout', 300);
    partial = [file '.part'];
    websave(partial, [cfg.server job.download], opts);
    movefile(partial, file, 'f');
end

function remove_others(cfg, old, file)
    for k = 1:numel(old)  % older exports of the same flight or log
        stale = fullfile(cfg.cache, old(k).name);
        if ~strcmp(stale, file)
            delete(stale);
        end
    end
end

function file = newest(cfg, old, label, err)
    if isempty(old)
        rethrow(err);
    end
    [~, k] = max([old.datenum]);
    file = fullfile(cfg.cache, old(k).name);
    warning('pprz_fetch:offline', '%s: using the cached copy (%s)', label, err.message);
end

function files = offline(ids, scope, cfg, err)
    % Without a connection: the newest cached file of each log the numbers belong to.
    index = known(cfg);
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
        files{k} = newest(cfg, dir(fullfile(cfg.cache, sprintf('log_%d_%s_*.mat', logs(k), scope))), ...
            sprintf('Log %d', logs(k)), err);
    end
end

function index = known(cfg)
    % flight number -> log id of earlier downloads (cache/flight_logs.txt), for offline use
    index = zeros(0, 2);
    name = fullfile(cfg.cache, 'flight_logs.txt');
    if isfile(name)
        fid = fopen(name, 'r');
        data = fscanf(fid, '%d %d', [2 Inf]);
        fclose(fid);
        index = data';
    end
end

function remember(cfg, flight_ids, log_id)
    index = known(cfg);
    flight_ids = double(flight_ids(:));
    index = [index(~ismember(index(:, 1), flight_ids), :); flight_ids, repmat(double(log_id), numel(flight_ids), 1)];
    fid = fopen(fullfile(cfg.cache, 'flight_logs.txt'), 'w');
    fprintf(fid, '%d %d\n', index');
    fclose(fid);
end
