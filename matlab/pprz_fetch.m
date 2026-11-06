function logs = pprz_fetch(ids, varargin)
%PPRZ_FETCH Load flights from the logbook server by their flight number (the number in /flight/<id>).
%   logs = pprz_fetch(1234)                  one flight
%   logs = pprz_fetch([1234 1240 1301])      several; see pprz_logbook for a table of the numbers
%   logs = pprz_fetch(ids, 'force', true)    download again even when a current copy is cached
%   f = pprz_flight(pprz_fetch(1234), 1)     ready to plot
%   Each flight is exported from its own log ("This flight" on the flight page) and kept in the cache folder
%   (pprz_server). A cached file is reused while the export would be identical; a change of the log, parser
%   or logbook data (crew, corrections) fetches it again. Without a connection the last cached copy is used.
%   Set the server once with pprz_server('https://logs.example.org', 'API-KEY').
    opt = struct('force', false);
    for k = 1:2:numel(varargin)
        opt.(lower(varargin{k})) = varargin{k + 1};
    end
    cfg = pprz_server();
    if ~isfolder(cfg.cache)
        mkdir(cfg.cache);
    end
    ids = unique(double(ids(:)'), 'stable');
    files = cell(1, numel(ids));
    for k = 1:numel(ids)
        files{k} = fetch_one(ids(k), cfg, opt.force);
    end
    logs = pprz_load(files{:});
end

function file = fetch_one(id, cfg, force)
    old = dir(fullfile(cfg.cache, sprintf('flight_%d_*.mat', id)));
    try
        job = pprz.api('POST', sprintf('/api/flights/%d/matlab', id), 'check', '1');
    catch err
        if isempty(old)
            rethrow(err);
        end
        [~, newest] = max([old.datenum]);
        file = fullfile(cfg.cache, old(newest).name);
        warning('pprz_fetch:offline', 'Flight %d: using the cached copy (%s)', id, err.message);
        return
    end
    file = fullfile(cfg.cache, sprintf('flight_%d_%s.mat', id, job.id(1:16)));
    if isfile(file) && ~force
        return
    end
    if ~strcmp(job.status, 'ready')
        job = pprz.api('POST', sprintf('/api/flights/%d/matlab', id), 'check', '0');
    end
    last = -1;
    while ~strcmp(job.status, 'ready')
        if strcmp(job.status, 'failed')
            error('pprz_fetch:failed', 'Flight %d: the export failed: %s', id, char(string(job.error)));
        end
        pct = round(100 * double(job.progress));
        if pct ~= last
            fprintf('Flight %d: preparing the export on the server, %d%%\n', id, pct);
            last = pct;
        end
        pause(1);
        job = pprz.api('GET', sprintf('/api/matlab-exports/%s', job.id));
    end
    fprintf('Flight %d: downloading %s\n', id, job.filename);
    opts = weboptions('HeaderFields', {'Authorization', ['Bearer ' cfg.key]}, 'Timeout', 300);
    partial = [file '.part'];
    websave(partial, [cfg.server job.download], opts);
    movefile(partial, file, 'f');
    for k = 1:numel(old)  % older exports of this flight
        stale = fullfile(cfg.cache, old(k).name);
        if ~strcmp(stale, file)
            delete(stale);
        end
    end
end
