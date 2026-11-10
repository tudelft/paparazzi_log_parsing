function files = pprz_download(ids, varargin)
%PPRZ_DOWNLOAD Download flights from the logbook server by number (as in /flight/<number>) and return local paths.
%   files = pprz_download(1234)                   the original log the flight was recorded in: path of its data
%                                                 file (.data with the .log next to it, .BFL, .ulg, .bin, …)
%   files = pprz_download([1234 1240], 'mat', true)   the flights' MATLAB exports (.mat), as pprz_fetch loads them
%   files = pprz_download(ids, 'force', true)     download again even when cached
%   s = pprz_load(pprz_download(1234){1})         convert a Paparazzi log locally (after pprz_setup)
%   files is a cell array of paths in the order of ids. Downloads are cached in pprz_server's cache folder, shared
%   with the Python package mavlab-logs: an original log is named by its SHA-256 (checked after the download) and
%   never goes stale; an export is fetched again when it would change (new log version, parser or logbook
%   corrections such as the crew). Without a connection the cached copies are used.
    opt = struct('mat', false, 'force', false);
    for k = 1:2:numel(varargin)
        opt.(lower(varargin{k})) = varargin{k + 1};
    end
    cfg = pprz_server();
    ids = double(ids(:)');
    files = cell(1, numel(ids));
    for k = 1:numel(ids)
        if opt.mat
            files{k} = mat_one(ids(k), cfg, opt.force);
        else
            files{k} = raw_one(ids(k), cfg, opt.force);
        end
    end
end

function file = raw_one(id, cfg, force)
    index = fullfile(cfg.cache, 'flights', pprz.config('tag', cfg.server), sprintf('%d.json', id));
    try
        info = pprz.api('GET', sprintf('/api/flights/%d/files', id));
    catch err
        if ~strcmp(err.identifier, 'pprz:offline') || ~isfile(index)
            rethrow(err);
        end
        info = jsondecode(fileread(index));
        file = fullfile(cfg.cache, 'logs', info.sha256, info.files(1).name);
        if ~all(arrayfun(@(f) isfile(fullfile(cfg.cache, 'logs', info.sha256, f.name)), info.files))
            rethrow(err);
        end
        warning('pprz_download:offline', 'Flight %d: using the cached copy (%s)', id, err.message);
        return
    end
    folder = fullfile(cfg.cache, 'logs', info.sha256);
    if ~isfolder(folder)
        mkdir(folder);
    end
    for f = reshape(info.files, 1, [])
        target = fullfile(folder, f.name);
        if isfile(target) && ~force
            continue
        end
        fprintf('Flight %d: downloading %s\n', id, f.name);
        partial = save_part(f.url, target);
        if strcmp(f.kind, 'data') && ~strcmp(pprz.sha256(partial), info.sha256)
            delete(partial);
            error('pprz_download:corrupt', 'Flight %d: %s does not match the server''s SHA-256', id, f.name);
        end
        movefile(partial, target, 'f');
    end
    write_text(index, jsonencode(info));
    file = fullfile(folder, info.files(1).name);
end

function file = mat_one(id, cfg, force)
    folder = fullfile(cfg.cache, 'mat', pprz.config('tag', cfg.server));
    old = dir(fullfile(folder, sprintf('flight_%d_*.mat', id)));
    path = sprintf('/api/flights/%d/matlab', id);
    try
        job = pprz.api('POST', path, struct('check', true));
    catch err
        if ~strcmp(err.identifier, 'pprz:offline') || isempty(old)
            rethrow(err);
        end
        [~, newest] = max([old.datenum]);
        file = fullfile(folder, old(newest).name);
        warning('pprz_download:offline', 'Flight %d: using the cached copy (%s)', id, err.message);
        return
    end
    file = fullfile(folder, sprintf('flight_%d_%s.mat', id, job.id(1:16)));
    if isfile(file) && ~force
        return
    end
    if ~strcmp(job.status, 'ready')
        job = pprz.api('POST', path, struct('check', false));
    end
    last = -1;
    while ~strcmp(job.status, 'ready')
        if any(strcmp(job.status, {'failed', 'missing'}))
            error('pprz_download:failed', 'Flight %d: the export failed: %s', id, char(string(job.error)));
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
    if ~isfolder(folder)
        mkdir(folder);
    end
    movefile(save_part(job.download, file), file, 'f');
    for k = 1:numel(old)  % older exports of this flight
        stale = fullfile(folder, old(k).name);
        if ~strcmp(stale, file)
            delete(stale);
        end
    end
end

function partial = save_part(url, target)
    % Accept-Encoding identity: the server then sends a gzip-stored log decompressed
    cfg = pprz_server();  % the key may be new: pprz.api signs in when needed
    opts = weboptions('HeaderFields', {'Authorization', ['Bearer ' cfg.key]; 'Accept-Encoding', 'identity'}, ...
        'Timeout', 300);
    partial = [target '.part'];
    websave(partial, [cfg.server url], opts);
end

function write_text(file, text)
    folder = fileparts(file);
    if ~isfolder(folder)
        mkdir(folder);
    end
    fid = fopen(file, 'w');
    fwrite(fid, text, 'char');
    fclose(fid);
end
