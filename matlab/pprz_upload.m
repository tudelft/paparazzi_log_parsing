function receipts = pprz_upload(files, varargin)
%PPRZ_UPLOAD Upload log files to the logbook server and return one receipt per log.
%   r = pprz_upload('24_10_30__16_27_37_SD.data')     a Paparazzi .data goes with the .log next to it (or the other
%                                                     way round); other formats (.BFL, .ulg, .bin, .tlog, …) alone
%   r = pprz_upload({'a.data', 'LOG00030.BFL'}, 'wait', true)   wait until the server has parsed each log
%   r = pprz_upload('LOG00030.BFL', 'name', 'Hex 1/LOG00030.BFL')   stored name; a folder in it can link the log
%                                                     to that fleet aircraft
%   pprz_download(r(1).flight_ids)                    the flights found in it (with 'wait', true)
%   A log you already uploaded is skipped (status "skipped") unless 'skip_existing', false. Receipts have name,
%   status (accepted, skipped or done), ingest_id and, with 'wait', log_id and flight_ids. Uses the server and
%   login of pprz_server (signing in through the browser when needed).
    opt = struct('name', '', 'wait', false, 'skip_existing', true, 'poll', 5, 'max_wait', 3600);
    for k = 1:2:numel(varargin)
        opt.(lower(varargin{k})) = varargin{k + 1};
    end
    files = cellstr(files);
    if ~isempty(opt.name) && numel(files) ~= 1
        error('pprz_upload:name', '''name'' can only be given for a single file');
    end
    groups = {};
    seen = {};
    for k = 1:numel(files)
        path = char(files{k});
        if ~isfile(path)
            error('pprz_upload:missing', 'No such file: %s', path);
        end
        [folder, stem, ext] = fileparts(path);
        stored = opt.name;
        if isempty(stored)
            stored = [stem ext];
        end
        stored = strrep(stored, '\', '/');
        group = struct('names', {{stored}}, 'paths', {{path}}, 'paired', false);
        if any(strcmpi(ext, {'.data', '.log'}))
            other = '.log';
            if strcmpi(ext, '.log')
                other = '.data';
            end
            companion = '';
            for c = {lower(other), upper(other)}
                if isfile(fullfile(folder, [stem c{1}]))
                    companion = fullfile(folder, [stem c{1}]);
                    break
                end
            end
            if ~isempty(companion)
                pair = {path, companion};
                if strcmpi(ext, '.log')
                    pair = pair([2 1]);
                end
                base = regexprep(stored, '\.[^./]*$', '');
                group = struct('names', {{[base '.data'], [base '.log']}}, 'paths', {pair}, 'paired', true);
            elseif strcmpi(ext, '.data')
                error('pprz_upload:missing', 'No matching .log next to %s', path);
            end  % else: a .log on its own (an ArduPilot text log)
        end
        id = char(java.io.File(group.paths{1}).getCanonicalPath());
        if ~any(strcmp(seen, id))
            seen{end + 1} = id; %#ok<AGROW>
            groups{end + 1} = group; %#ok<AGROW>
        end
    end
    receipts = repmat(struct('name', '', 'status', '', 'ingest_id', [], 'log_id', [], 'flight_ids', []), 0, 1);
    for k = 1:numel(groups)
        g = groups{k};
        r = struct('name', g.names{1}, 'status', '', 'ingest_id', [], 'log_id', [], 'flight_ids', []);
        if opt.skip_existing
            digest = pprz.sha256(g.paths{1});
            known = pprz.api('POST', '/api/logs/check', struct('sha256', {{digest}})).known;
            field = matlab.lang.makeValidName(digest);  % jsondecode turns the hash key into a field name
            if isstruct(known) && isfield(known, field)
                item = known.(field);
                if (~isfield(item, 'mine') || item.mine) && (item.has_log || ~g.paired)
                    fprintf('%s: already on the server\n', r.name);
                    r.status = 'skipped';
                    receipts(end + 1, 1) = r; %#ok<AGROW>
                    continue
                end
            end
        end
        fprintf('%s: uploading\n', r.name);
        args = {};
        for n = 1:numel(g.names)
            args = [args, {'names', g.names{n}}]; %#ok<AGROW>
        end
        for n = 1:numel(g.paths)
            args = [args, {'files', matlab.net.http.io.FileProvider(g.paths{n})}]; %#ok<AGROW>
        end
        out = pprz.api('POST', '/api/upload', matlab.net.http.io.MultipartFormProvider(args{:}));
        result = out.results;
        if iscell(result)
            result = result{1};
        end
        if numel(result) ~= 1 || ~strcmp(result.status, 'accepted')
            reason = '';
            if isfield(result, 'reason')
                reason = result.reason;
            end
            error('pprz_upload:rejected', '%s: upload not accepted: %s', r.name, reason);
        end
        r.status = 'accepted';
        r.ingest_id = result.ingest_id;
        if opt.wait
            done = wait_parsed(r.ingest_id, opt.poll, opt.max_wait);
            r.status = 'done';
            r.log_id = done.log_id;
            if isfield(done, 'flight_ids')
                r.flight_ids = reshape(done.flight_ids, 1, []);
            end
        end
        receipts(end + 1, 1) = r; %#ok<AGROW>
    end
end

function result = wait_parsed(ingest_id, poll, max_wait)
    started = tic;
    last = '';
    while true
        result = pprz.api('GET', sprintf('/api/ingest/%d', ingest_id));
        state = char(string(result.status));
        parsed = '';
        if isfield(result, 'log_status') && ~isempty(result.log_status)
            parsed = char(result.log_status);
        end
        if any(strcmp(state, {'error', 'rejected'}))
            error('pprz_upload:failed', 'Upload %d %s: %s', ingest_id, state, char(string(result.error)));
        end
        if any(strcmp(state, {'stored', 'duplicate'}))
            if strcmp(parsed, 'done')
                return
            elseif strcmp(parsed, 'error')
                error('pprz_upload:failed', 'Upload %d: parsing failed: %s', ingest_id, char(string(result.log_error)));
            elseif isempty(parsed)
                error('pprz_upload:failed', 'Upload %d: the stored log was deleted before it was parsed', ingest_id);
            end
        end
        if toc(started) > max_wait
            error('pprz_upload:timeout', 'Timed out waiting for upload %d; the server may still be working', ingest_id);
        end
        now_state = [state ' ' parsed];
        if ~strcmp(now_state, last)
            fprintf('Upload %d: %s\n', ingest_id, strtrim(now_state));
            last = now_state;
        end
        pause(poll);
    end
end
