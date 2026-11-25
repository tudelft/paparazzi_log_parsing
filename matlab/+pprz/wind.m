function w = wind(d)
%WIND The wind in use in one form for old and new logs: w = pprz.wind(f.data)
%   Reads AIR_DATA.wind (pprzlink 2026-11: north/east/down, NaN if unknown, source in wind_id) or else the older
%   WIND_INFO_RET (east/north/up with flags: bit 0 horizontal, bit 1 vertical, bit 2 airspeed, bit 3 temperature).
%   Both give t, north, east, up [m/s] (the direction the wind blows to; NaN where not valid), speed (horizontal),
%   from [deg] (the compass direction the wind comes from), id (ABI id of the source, NaN in older logs),
%   airspeed [m/s] (WIND_INFO_RET only, else NaN) and source (the message name). Without wind w.t is empty.
%   The per-source wind of newer logs is WIND_RAW (sensor_id, wind north/east/down), see plot_wind.
    w = struct('t', zeros(0, 1), 'north', zeros(0, 1), 'east', zeros(0, 1), 'up', zeros(0, 1), ...
        'id', zeros(0, 1), 'airspeed', zeros(0, 1), 'source', '');
    if pprz.has(d, 'AIR_DATA.wind')
        a = d.AIR_DATA;
        v = double(a.wind);
        w.t = double(a.t(:));
        w.north = v(:, 1); w.east = v(:, 2); w.up = -v(:, 3);
        w.id = nan(size(w.t));
        if isfield(a, 'wind_id'), w.id = double(a.wind_id(:)); end
        w.airspeed = nan(size(w.t));
        w.source = 'AIR_DATA';
    elseif pprz.has(d, 'WIND_INFO_RET')
        m = d.WIND_INFO_RET;
        w.t = double(m.t(:));
        flags = double(m.flags(:));
        w.north = double(m.north(:)); w.east = double(m.east(:)); w.up = double(m.up(:));
        w.north(~bitand(flags, 1)) = NaN; w.east(~bitand(flags, 1)) = NaN; w.up(~bitand(flags, 2)) = NaN;
        w.id = nan(size(w.t));
        w.airspeed = nan(size(w.t));
        if isfield(m, 'airspeed')
            w.airspeed = double(m.airspeed(:));
            w.airspeed(~bitand(flags, 4)) = NaN;
        end
        w.source = 'WIND_INFO_RET';
    end
    w.speed = hypot(w.east, w.north);
    w.from = mod(rad2deg(atan2(-w.east, -w.north)), 360);
end
