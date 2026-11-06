function T = pprz_logbook(varargin)
%PPRZ_LOGBOOK Table of the logbook flights the API key may see, newest first, to pick numbers for pprz_fetch.
%   T = pprz_logbook()                    flights judged real by the parser
%   T = pprz_logbook('all', true)         also uncertain and ground records
%   logs = pprz_fetch(T.flight(contains(T.aircraft, 'Hex') & T.date > datetime(2026, 10, 1)))
%   Columns: flight (number for pprz_fetch), date (takeoff, UTC), aircraft, duration_min, pic, project, goal,
%   verdict and log. Paper records are left out (they have no telemetry to export).
    opt = struct('all', false);
    for k = 1:2:numel(varargin)
        opt.(lower(varargin{k})) = varargin{k + 1};
    end
    rows = pprz.api('GET', '/api/logbook?compact=1');
    if isstruct(rows)
        rows = num2cell(rows);
    end
    keep = cellfun(@(r) isfield(r, 'flight_id') && ~isempty(r.flight_id) && isnumeric(r.flight_id), rows);
    rows = rows(keep);
    n = numel(rows);
    flight = zeros(n, 1);
    date = NaT(n, 1, 'TimeZone', 'UTC');
    duration_min = nan(n, 1);
    [aircraft, pic, project, goal, verdict] = deal(strings(n, 1));
    log = zeros(n, 1);
    for k = 1:n
        r = rows{k};
        flight(k) = r.flight_id;
        utc = char(field(r, 'takeoff_utc'));
        if numel(utc) >= 19  % ISO 8601, e.g. 2026-11-05T09:59:46.627Z: seconds are enough here
            date(k) = datetime(utc(1:19), 'InputFormat', 'yyyy-MM-dd''T''HH:mm:ss', 'TimeZone', 'UTC');
        end
        if isnumeric(field(r, 'duration_s')) && ~isempty(field(r, 'duration_s'))
            duration_min(k) = r.duration_s / 60;
        end
        aircraft(k) = string(field(r, 'aircraft'));
        person = field(r, 'pic_user');
        if isstruct(person) && isfield(person, 'display_name')
            pic(k) = string(person.display_name);
        else
            pic(k) = string(field(r, 'pic'));
        end
        project(k) = string(field(r, 'project_name'));
        goal(k) = string(field(r, 'goal'));
        verdict(k) = string(field(r, 'verdict'));
        if isnumeric(field(r, 'log_id')) && ~isempty(field(r, 'log_id'))
            log(k) = r.log_id;
        end
    end
    T = table(flight, date, aircraft, duration_min, pic, project, goal, verdict, log);
    if ~opt.all
        T = T(~ismember(T.verdict, ["uncertain", "ground"]), :);
    end
end

function v = field(r, name)
    if isfield(r, name) && ~isempty(r.(name))
        v = r.(name);
    else
        v = '';
    end
end
