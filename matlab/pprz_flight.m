function f = pprz_flight(logs, varargin)
%PPRZ_FLIGHT One flight (or whole log) ready for plotting.
%   f = pprz_flight(logs, n)          row n of pprz_flights(logs)
%   f = pprz_flight(logs, l, a, k)    log l, aircraft a, flight k (k = 0: the aircraft's whole log)
%   f = pprz_flight(s)                the first flight with data, else the whole log
%
%   f.data holds the messages (time 0 at takeoff; log time for a whole log). A flight listed in a whole-log
%   export is cut from the whole log (10 s margins). f also has title, time, summary, index, aircraft
%   (id, name, crew, settings, checklist, config...), log and messages (units, coef, enums).
    if nargin == 2
        T = pprz_flights(logs);
        r = T(varargin{1}, :);
        [l, a, k] = deal(r.log, r.aircraft, r.flight);
    elseif nargin == 4
        [l, a, k] = deal(varargin{:});
    else
        T = pprz_flights(logs);
        r = T(find(T.has_data & T.flight > 0, 1), :);
        if isempty(r)
            r = T(find(T.has_data, 1), :);
        end
        [l, a, k] = deal(r.log, r.aircraft, r.flight);
    end
    s = logs(l);
    ac = s.aircraft(a);
    [~, file] = fileparts(s.file);
    f = struct();
    f.log = s.log;
    f.log.file = s.file;
    f.messages = s.messages;
    f.aircraft = rmfield(ac, intersect(fieldnames(ac), {'flights', 'data', 'unparsed'}));
    if k == 0
        f.title = sprintf('%s · %s · whole log', file, ac.name);
        f.index = 0;
        f.time = struct('offset_s', 0, 'start_s', -Inf, 'end_s', Inf);
        f.summary = struct();
        f.data = ac.data;
        return
    end
    fl = ac.flights(k);
    f.title = sprintf('%s · %s · flight %d', file, ac.name, fl.index);
    f.index = fl.index;
    f.time = fl.time;
    f.summary = fl.summary;
    if isfield(fl, 'data')
        f.data = fl.data;
    elseif isfield(ac, 'data')
        f.data = pprz.crop(ac.data, fl.time.start_s, fl.time.end_s, fl.time.offset_s);
    else
        error('pprz_flight:nodata', 'This export lists flight %d without data.', fl.index);
    end
end
