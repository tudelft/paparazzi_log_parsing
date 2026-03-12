function match = match_c2c_dronetag(c2c, tag, verbose)
%MATCH_C2C_DRONETAG Identify which C2C callsign corresponds to a dronetag log
%                   and align the two tracks in time.
%
%   MATCH = MATCH_C2C_DRONETAG(C2C, TAG)
%   MATCH = MATCH_C2C_DRONETAG(C2C, TAG, VERBOSE)
%
%   Inputs:
%     c2c     - Full output struct from parse_c2c (all callsigns)
%     tag     - Output struct from parse_dronetag
%     verbose - (optional, default true) Print per-callsign scoring details
%
%   Output:
%     match        - Struct with fields:
%       .callsign           - Name of the best-matching callsign (string)
%       .time               - Common UTC datetime vector (C2C timestamps)
%       .time_sec           - Seconds from first common timestamp
%       .overlap_duration_s - Duration of common time window (s)
%       .pos_error_m        - Horizontal position error between tracks (m)
%       .c2c                - C2C fields resampled onto .time
%           .lat                - Latitude  (deg)
%           .lon                - Longitude (deg)
%           .alt                - Altitude AMSL (m)
%           .vel_east           - Ground speed East (m/s)
%           .vel_north          - Ground speed North (m/s)
%           .vel_up             - Ground speed Up (m/s)
%           .vel_mag            - Speed magnitude (m/s)
%       .tag                - Dronetag fields interpolated onto .time
%           .lat                - Latitude  (deg)
%           .lon                - Longitude (deg)
%           .alt                - Altitude (m)
%           .vel_x              - Velocity X (m/s)
%           .vel_y              - Velocity Y (m/s)
%           .vel_z              - Velocity Z (m/s)
%           .vel_mag            - Speed magnitude (m/s)
%
%   Every callsign in c2c is tried. The one with the lowest mean horizontal
%   position error over the overlapping time window is returned.
%
%   Example:
%     c2c   = parse_c2c('Logs/26_02_25_EHVB');
%     tag   = parse_dronetag('Logs/26_02_25_EHVB/26_02_25__16_30_00.json');
%     match = match_c2c_dronetag(c2c, tag);
%     match = match_c2c_dronetag(c2c, tag, false);  % silent

    if nargin < 3
        verbose = true;
    end

    %% --- Validate inputs -----------------------------------------------
    if ~isstruct(c2c)
        error('match_c2c_dronetag:badInput', 'c2c must be the output struct from parse_c2c.');
    end
    if ~isstruct(tag) || ~isfield(tag, 'time')
        error('match_c2c_dronetag:badInput', 'tag must be the output struct from parse_dronetag.');
    end

    callsigns = fieldnames(c2c);
    if isempty(callsigns)
        error('match_c2c_dronetag:empty', 'c2c struct has no callsign fields.');
    end

    %% --- Score every callsign ------------------------------------------
    best_error  = Inf;
    best_name   = '';
    best_result = [];

    if verbose
        fprintf('Dronetag time range: %s  →  %s\n', char(tag.time(1)), char(tag.time(end)));
        fprintf('Scoring %d callsign(s) against dronetag...\n', numel(callsigns));
    end

    for ci = 1:numel(callsigns)
        name = callsigns{ci};
        try
            result = align_one(c2c.(name), tag, verbose);
        catch ME
            if verbose
                fprintf('  %-20s  (skipped: %s)\n', name, ME.message);
            end
            continue;
        end

        mean_err = mean(result.pos_error_m, 'omitnan');
        if verbose
            fprintf('  %-20s  overlap=%.0f s  mean_pos_err=%.1f m\n', ...
                name, result.overlap_duration_s, mean_err);
        end

        if mean_err < best_error
            best_error  = mean_err;
            best_name   = name;
            best_result = result;
        end
    end

    if isempty(best_name)
        error('match_c2c_dronetag:noMatch', ...
            'No callsign had a valid time overlap with the dronetag log.');
    end

    match          = best_result;
    match.callsign = best_name;

    fprintf('\nBest match: %s  (mean pos error = %.1f m,  max = %.1f m)\n', ...
        best_name, best_error, max(match.pos_error_m, [], 'omitnan'));
end

%% -------------------------------------------------------------------------
function result = align_one(cs, tag, verbose)
%ALIGN_ONE Internal: align one callsign struct against dronetag.

    c2c_time = cs.json_time;
    % Normalise to UTC so comparison with dronetag (always UTC) works
    if isempty(c2c_time.TimeZone)
        c2c_time.TimeZone = 'UTC';
    else
        c2c_time = datetime(c2c_time, 'TimeZone', 'UTC');
    end
    if isempty(tag.time.TimeZone)
        tag.time.TimeZone = 'UTC';
    end
    valid    = ~isnat(c2c_time);
    c2c_time = c2c_time(valid);

    % sys_time = Amsterdam local time when C2C logged the message (arrival time)
    sys_time = cs.sys_time(valid);
    sys_posix = posixtime(datetime(sys_time, 'TimeZone', 'UTC'));

    c2c_lat  = cs.payload.location.latitude(valid);
    c2c_lon  = cs.payload.location.longitude(valid);
    c2c_alt  = cs.payload.location.altitudeAMSL(valid);
    c2c_ve   = cs.payload.groundSpeed.east(valid);
    c2c_vn   = cs.payload.groundSpeed.north(valid);
    c2c_vu   = cs.payload.groundSpeed.up(valid);

    [c2c_time, idx] = sort(c2c_time);
    sys_posix = sys_posix(idx);
    c2c_lat = c2c_lat(idx);  c2c_lon = c2c_lon(idx);  c2c_alt = c2c_alt(idx);
    c2c_ve  = c2c_ve(idx);   c2c_vn  = c2c_vn(idx);   c2c_vu  = c2c_vu(idx);

    % Collapse duplicate json_time stamps: average position/velocity,
    % keep earliest arrival time (min sys_time) per group.
    [c2c_time, ~, grp] = unique(c2c_time);
    sys_posix = accumarray(grp, sys_posix, [], @min);
    c2c_lat = accumarray(grp, c2c_lat, [], @mean);
    c2c_lon = accumarray(grp, c2c_lon, [], @mean);
    c2c_alt = accumarray(grp, c2c_alt, [], @mean);
    c2c_ve  = accumarray(grp, c2c_ve,  [], @mean);
    c2c_vn  = accumarray(grp, c2c_vn,  [], @mean);
    c2c_vu  = accumarray(grp, c2c_vu,  [], @mean);
    sys_time_utc = datetime(sys_posix, 'ConvertFrom', 'posixtime', 'TimeZone', 'UTC');

    if verbose
        fprintf('    C2C valid=%d unique=%d  range: %s -> %s\n', sum(valid), numel(c2c_time), ...
            char(c2c_time(1)), char(c2c_time(end)));
    end

    t_start = max(c2c_time(1),   tag.time(1));
    t_end   = min(c2c_time(end), tag.time(end));
    if t_start >= t_end
        error('no overlap');
    end

    in_win        = c2c_time >= t_start & c2c_time <= t_end;
    c2c_time      = c2c_time(in_win);
    sys_time_utc  = sys_time_utc(in_win);
    c2c_lat  = c2c_lat(in_win);  c2c_lon = c2c_lon(in_win);  c2c_alt = c2c_alt(in_win);
    c2c_ve   = c2c_ve(in_win);   c2c_vn  = c2c_vn(in_win);   c2c_vu  = c2c_vu(in_win);

    if numel(c2c_time) < 2
        error('too few points');
    end

    tag_t   = seconds(tag.time  - t_start);
    c2c_t   = seconds(c2c_time  - t_start);
    win_tag = tag_t >= 0 & tag_t <= seconds(t_end - t_start);
    tt      = tag_t(win_tag);

    % Deduplicate tag samples (same fix broadcast multiple times)
    [tt, ~, ugrp] = unique(tt);
    dedup = @(v) accumarray(ugrp, v(win_tag), [], @mean);
    tl = dedup(tag.lat);       tlo = dedup(tag.lon);
    ta = dedup(tag.altitude);  tvx = dedup(tag.velocity_x);
    tvy = dedup(tag.velocity_y); tvz = dedup(tag.velocity_z);
    % Also interpolate the tag GPS timestamp itself (as posixtime, then back)
    tag_posix = dedup(posixtime(tag.time));

    i_lat  = interp1(tt, tl,        c2c_t, 'linear');
    i_lon  = interp1(tt, tlo,       c2c_t, 'linear');
    i_alt  = interp1(tt, ta,        c2c_t, 'linear');
    i_vx   = interp1(tt, tvx,       c2c_t, 'linear');
    i_vy   = interp1(tt, tvy,       c2c_t, 'linear');
    i_vz   = interp1(tt, tvz,       c2c_t, 'linear');
    i_time = datetime(interp1(tt, tag_posix, c2c_t, 'linear'), ...
                      'ConvertFrom', 'posixtime', 'TimeZone', 'UTC');

    result.time               = c2c_time;
    result.time_sec           = c2c_t;
    result.overlap_duration_s = seconds(t_end - t_start);
    result.pos_error_m        = haversine_m(c2c_lat, c2c_lon, i_lat, i_lon);

    result.c2c.sys_time   = sys_time_utc;  % UTC arrival time at C2C receiver
    result.c2c.lat        = c2c_lat;  result.c2c.lon       = c2c_lon;
    result.c2c.alt        = c2c_alt;
    result.c2c.vel_east   = c2c_ve;   result.c2c.vel_north = c2c_vn;
    result.c2c.vel_up     = c2c_vu;
    result.c2c.vel_mag    = sqrt(c2c_ve.^2 + c2c_vn.^2 + c2c_vu.^2);

    result.tag.time   = i_time;
    result.tag.lat    = i_lat;  result.tag.lon   = i_lon;
    result.tag.alt    = i_alt;
    result.tag.vel_x  = i_vx;  result.tag.vel_y = i_vy;  result.tag.vel_z = i_vz;
    result.tag.vel_mag = sqrt(i_vx.^2 + i_vy.^2 + i_vz.^2);
end

%% -------------------------------------------------------------------------
function d = haversine_m(lat1, lon1, lat2, lon2)
%HAVERSINE_M Horizontal distance in metres between two lat/lon arrays.
    R    = 6371000;
    dlat = deg2rad(lat2 - lat1);
    dlon = deg2rad(lon2 - lon1);
    a    = sin(dlat/2).^2 + cos(deg2rad(lat1)).*cos(deg2rad(lat2)).*sin(dlon/2).^2;
    d    = 2 * R * asin(sqrt(a));
end
