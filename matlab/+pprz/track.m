function tr = track(f, by)
%TRACK The flown path of a flight with one value per point to colour it by.
%   tr = pprz.track(f, 'altitude')   by: 'altitude', 'speed', 'mode' (autopilot) or 'battery'
%   tr.t, tr.lat, tr.lon        time [s] and position [deg]; lat/lon from GPS_INT, else from the local
%                               ROTORCRAFT_FP position and the flight plan origin (empty when neither is known)
%   tr.east, tr.north           position [m] from tr.origin (flight plan lat0/lon0, else the first GPS point)
%   tr.value, tr.label          the colouring value per point and its label; NaN where it is not logged
%   tr.names                    for 'mode': the mode name of value k is tr.names{k}
%   tr.waypoints                struct array name, lat, lon, east, north of the flight plan waypoints
%   tr.shapes                   navigation circles and segments flown (CIRCLE, SEGMENT) and moved waypoints
%                               (WP_MOVED_ENU): struct array kind, lat, lon, east, north
    if nargin < 2
        by = 'altitude';
    end
    d = f.data;
    fp = struct();
    if isfield(f, 'aircraft') && isfield(f.aircraft, 'config') && isfield(f.aircraft.config, 'flight_plan')
        fp = f.aircraft.config.flight_plan;
    end
    tr = struct('t', zeros(0, 1), 'lat', [], 'lon', [], 'east', zeros(0, 1), 'north', zeros(0, 1), ...
        'origin', [NaN NaN], 'value', zeros(0, 1), 'label', '', 'names', {{}}, 'source', '', ...
        'waypoints', struct('name', {}, 'lat', {}, 'lon', {}, 'east', {}, 'north', {}), ...
        'shapes', struct('kind', {}, 'lat', {}, 'lon', {}, 'east', {}, 'north', {}));
    if isfield(fp, 'lat0') && isfield(fp, 'lon0') && isfinite(fp.lat0) && isfinite(fp.lon0)
        tr.origin = [fp.lat0, fp.lon0];
    end

    gps = pprz.has(d, 'GPS_INT') && isfield(d.GPS_INT, 'lat') && isfield(d.GPS_INT, 'lon');
    if gps
        g = d.GPS_INT;
        ok = double(g.lat) ~= 0 & double(g.lon) ~= 0;
        gps = any(ok);
    end
    if gps
        tr.t = g.t(ok);
        tr.lat = double(g.lat(ok));
        tr.lon = double(g.lon(ok));
        tr.source = 'GPS_INT';
        if any(isnan(tr.origin))
            tr.origin = [tr.lat(1), tr.lon(1)];
        end
        [tr.east, tr.north] = local(tr.lat, tr.lon, tr.origin);
    elseif pprz.has(d, 'ROTORCRAFT_FP')
        p = d.ROTORCRAFT_FP;
        tr.t = p.t;
        tr.east = double(p.east);
        tr.north = double(p.north);
        tr.source = 'ROTORCRAFT_FP';
        if ~any(isnan(tr.origin))
            [tr.lat, tr.lon] = global_of(tr.east, tr.north, tr.origin);
        end
    else
        return
    end

    switch by
        case 'altitude'
            if pprz.has(d, 'ROTORCRAFT_FP') && isfield(d.ROTORCRAFT_FP, 'up')
                tr.value = at(d.ROTORCRAFT_FP.t, d.ROTORCRAFT_FP.up, tr.t);
                tr.label = 'Height [m]';
            elseif gps && isfield(g, 'hmsl')
                tr.value = double(g.hmsl(ok));
                tr.label = 'Altitude MSL [m]';
            end
        case 'speed'
            dt = diff(tr.t);
            v = hypot(diff(tr.east), diff(tr.north)) ./ dt;
            v(dt <= 0) = NaN;
            v = [v; v(end)];
            tr.value = movmean(v, 5, 'omitnan');
            tr.label = 'Ground speed [m/s]';
        case 'mode'
            for msg = {'ROTORCRAFT_STATUS', 'PPRZ_MODE'}
                if pprz.has(d, msg{1}) && isfield(d.(msg{1}), 'ap_mode')
                    m = d.(msg{1});
                    raw = at(m.t, double(m.ap_mode), tr.t, 'previous');
                    modes = unique(raw(~isnan(raw)));
                    names = pprz.enum(f, msg{1}, 'ap_mode', modes);
                    tr.names = arrayfun(@(k) char(names(k)), 1:numel(modes), 'UniformOutput', false);
                    [~, tr.value] = ismember(raw, modes);
                    tr.value = double(tr.value);
                    tr.value(tr.value == 0) = NaN;
                    tr.label = 'Autopilot mode';
                    break
                end
            end
        case 'battery'
            sources = {'ENERGY', 'voltage'; 'ENERGY', 'bat'; 'ROTORCRAFT_STATUS', 'vsupply'};
            for k = 1:size(sources, 1)
                if pprz.has(d, sources{k, 1}) && isfield(d.(sources{k, 1}), sources{k, 2})
                    m = d.(sources{k, 1});
                    tr.value = at(m.t, double(m.(sources{k, 2})), tr.t);
                    tr.label = 'Battery [V]';
                    break
                end
            end
        otherwise
            error('pprz:track', 'Colour by altitude, speed, mode or battery, not %s.', by);
    end
    if isempty(tr.value)
        tr.value = NaN(size(tr.t));
    end

    if isfield(fp, 'waypoints') && isstruct(fp.waypoints)
        for name = reshape(fieldnames(fp.waypoints), 1, [])
            w = fp.waypoints.(name{1});
            if ~isfield(w, 'lat') || ~isfinite(w.lat) || ~isfinite(w.lon) || any(isnan(tr.origin))
                continue
            end
            [e, n] = local(w.lat, w.lon, tr.origin);
            label = name{1};
            if isfield(w, 'name') && ~isempty(w.name)
                label = char(w.name);
            end
            tr.waypoints(end + 1) = struct('name', label, 'lat', w.lat, 'lon', w.lon, 'east', e, 'north', n);
        end
    end
    if ~any(isnan(tr.origin))
        tr.shapes = shapes(d, tr.origin);
    end
end

function out = shapes(d, origin)
    % Navigation circles, segments and moved waypoints; they are local to the navigation origin (INS_REF).
    out = struct('kind', {}, 'lat', {}, 'lon', {}, 'east', {}, 'north', {});
    nav = origin;
    if pprz.has(d, 'INS_REF') && double(d.INS_REF.lat0(end)) ~= 0
        nav = double([d.INS_REF.lat0(end), d.INS_REF.lon0(end)]);
    end
    pieces = {};
    if pprz.has(d, 'CIRCLE')
        c = unique(round(double([d.CIRCLE.center_east, d.CIRCLE.center_north, d.CIRCLE.radius]), 1), 'rows');
        a = linspace(0, 2 * pi, 73)';
        for k = 1:min(size(c, 1), 30)
            pieces(end + 1, :) = {'circle', c(k, 1) + c(k, 3) * cos(a), c(k, 2) + c(k, 3) * sin(a)}; %#ok<AGROW>
        end
    end
    if pprz.has(d, 'SEGMENT')
        g = d.SEGMENT;
        sg = unique(round(double([g.segment_east_1, g.segment_north_1, g.segment_east_2, g.segment_north_2]), 1), 'rows');
        for k = 1:min(size(sg, 1), 30)
            pieces(end + 1, :) = {'segment', sg(k, [1 3])', sg(k, [2 4])'}; %#ok<AGROW>
        end
    end
    if pprz.has(d, 'WP_MOVED_ENU')
        m = d.WP_MOVED_ENU;
        for id = reshape(unique(m.wp_id), 1, [])
            last = find(m.wp_id == id, 1, 'last');
            pieces(end + 1, :) = {'moved', double(m.east(last)), double(m.north(last))}; %#ok<AGROW>
        end
    end
    for k = 1:size(pieces, 1)
        [lat, lon] = global_of(pieces{k, 2}, pieces{k, 3}, nav);
        [e, n] = local(lat, lon, origin);
        out(end + 1) = struct('kind', pieces{k, 1}, 'lat', lat, 'lon', lon, 'east', e, 'north', n); %#ok<AGROW>
    end
end

function v = at(t, x, tq, method)
    % x at times tq: linear (or 'previous'), NaN outside the logged span.
    if nargin < 4
        method = 'linear';
    end
    [t, i] = unique(double(t(:)));
    x = double(x(i));
    if numel(t) < 2
        v = NaN(size(tq));
        return
    end
    v = interp1(t, x, double(tq), method, NaN);
end

function [east, north] = local(lat, lon, origin)
    % Equirectangular: well within a metre over a flying field.
    east = (lon - origin(2)) * cosd(origin(1)) * 111320;
    north = (lat - origin(1)) * 110574;
end

function [lat, lon] = global_of(east, north, origin)
    lat = origin(1) + north / 110574;
    lon = origin(2) + east / (cosd(origin(1)) * 111320);
end
