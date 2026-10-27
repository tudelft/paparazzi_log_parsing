function T = pprz_flights(logs)
%PPRZ_FLIGHTS Table of the flights in loaded exports, one row per flight; use a row number with pprz_flight.
%   T = pprz_flights(logs)
%   Columns: log, aircraft, flight (positions in logs(log).aircraft(aircraft).flights(flight); flight 0 is the
%   whole log of that aircraft in a whole-log export), file, ac_id, name, index (flight number in its log),
%   takeoff_utc, duration_s, verdict and has_data.
    rows = cell(0, 11);
    for l = 1:numel(logs)
        [~, file] = fileparts(logs(l).file);
        for a = 1:numel(logs(l).aircraft)
            ac = logs(l).aircraft(a);
            whole = isfield(ac, 'data');
            if whole
                rows(end + 1, :) = {l, a, 0, string(file), ac.id, string(ac.name), 0, "", NaN, "whole log", true}; %#ok<AGROW>
            end
            for k = 1:numel(ac.flights)
                fl = ac.flights(k);
                rows(end + 1, :) = {l, a, k, string(file), ac.id, string(ac.name), fl.index, ...
                    string(fl.time.takeoff_utc), fl.summary.duration_s, string(fl.summary.verdict), ...
                    whole || isfield(fl, 'data')}; %#ok<AGROW>
            end
        end
    end
    T = cell2table(rows, 'VariableNames', {'log', 'aircraft', 'flight', 'file', 'ac_id', 'name', 'index', ...
        'takeoff_utc', 'duration_s', 'verdict', 'has_data'});
end
