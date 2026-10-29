function fig = pprz_replay(f, model)
%PPRZ_REPLAY Replay a flight in 3-D with the airframe's model: attitude, moving control surfaces and motor
%   throttle (red-green), with the track seen from above and the main values at that time.
%   pprz_replay(f)                  f from pprz_flight; the model is picked from the aircraft name
%   pprz_replay(f, 'Nederdrone5')   a model in 3d_animation/3d_models (Nederdrone5, RotatingWingV3, Cyclone2),
%                                   a path to such a .mat file, or 'generic' for a plain quadrotor
%   Play/pause, drag the slider or use the arrow keys. Other views can drive it:
%   show = getappdata(fig, 'pprz_show'); show(t)   (e.g. from pprz.Cursor: c.listen(show))
    if nargin < 2
        model = '';
    end
    [M, model_name] = load_model(f, model);
    [t, quat] = attitude(f.data);
    if isempty(t)
        error('pprz_replay:noattitude', 'No attitude logged (AHRS_REF_QUAT, ROTORCRAFT_FP or ATTITUDE).');
    end
    act = [];
    if pprz.has(f.data, 'ACTUATORS')
        [act.values, ~, act.t] = pprz.actuators(f.data);
    end
    hud = hud_signals(f.data);

    fig = figure('Name', sprintf('3-D replay - %s', f.title), 'NumberTitle', 'off', 'Color', 'w', ...
        'Position', [100 100 1000 640]);
    ax = axes('Parent', fig, 'Position', [0 0.1 0.7 0.9]);
    hold(ax, 'on');
    % World frame NED (x north, y east, z down) drawn right-handed: y and z reversed.
    set(ax, 'YDir', 'reverse', 'ZDir', 'reverse', 'DataAspectRatio', [1 1 1], 'Visible', 'off');
    body = hgtransform('Parent', ax);
    for i = 1:numel(M.Aircraft)
        patch(M.Aircraft(i).stl_data, 'Parent', body, 'FaceColor', M.Aircraft(i).color, 'EdgeColor', 'none', ...
            'FaceLighting', 'gouraud', 'AmbientStrength', 0.3);
    end
    [parts, pieces] = add_controls(M, ax);
    R = 0;  % the size of the whole model, to frame the view
    for part = [reshape({M.Aircraft.stl_data}, 1, []), controls_data(M)]
        R = max([R; sqrt(sum(part{1}.vertices .^ 2, 2))]);
    end
    draw_frame(ax, R);
    axis(ax, 1.05 * R * [-1 1 -1 1 -1 1]);
    view(ax, [-140 25]);
    light('Parent', ax, 'Position', [-1 -1 -2], 'Style', 'infinite');

    info = uicontrol(fig, 'Style', 'text', 'Units', 'normalized', 'Position', [0.71 0.62 0.28 0.36], ...
        'HorizontalAlignment', 'left', 'FontName', 'FixedWidth', 'FontSize', 10, 'BackgroundColor', 'w');
    top = axes('Parent', fig, 'Position', [0.74 0.16 0.24 0.42]);
    tr = struct('t', []);  % the local position, seen from above
    if pprz.has(f.data, 'ROTORCRAFT_FP') && all(isfield(f.data.ROTORCRAFT_FP, {'east', 'north'}))
        tr = struct('t', f.data.ROTORCRAFT_FP.t, 'east', double(f.data.ROTORCRAFT_FP.east), ...
            'north', double(f.data.ROTORCRAFT_FP.north));
    end
    dot = [];
    if ~isempty(tr.t)
        plot(top, tr.east, tr.north, 'Color', [0.6 0.6 0.6]);
        hold(top, 'on');
        dot = plot(top, tr.east(1), tr.north(1), 'o', 'MarkerFaceColor', [0.85 0.15 0.1], 'MarkerEdgeColor', 'k');
        axis(top, 'equal');
        grid(top, 'on');
        xlabel(top, 'East [m]');
        ylabel(top, 'North [m]');
    else
        axis(top, 'off');
    end
    title(top, sprintf('Model: %s', model_name), 'Interpreter', 'none');

    slider = uicontrol(fig, 'Style', 'slider', 'Units', 'normalized', 'Position', [0.12 0.03 0.58 0.04], ...
        'Min', t(1), 'Max', max(t(end), t(1) + 0.1), 'Value', t(1), 'Callback', @(s, ~) show(get(s, 'Value')));
    play = uicontrol(fig, 'Style', 'togglebutton', 'String', 'Play', 'Units', 'normalized', ...
        'Position', [0.02 0.03 0.08 0.05], 'Callback', @(b, ~) run(b));
    speed = uicontrol(fig, 'Style', 'popupmenu', 'String', {'0.25x', '0.5x', '1x', '2x', '5x', '10x'}, ...
        'Value', 3, 'Units', 'normalized', 'Position', [0.72 0.03 0.08 0.05]);
    set(fig, 'WindowKeyPressFcn', @(~, evt) key(evt));
    setappdata(fig, 'pprz_show', @show);
    now_t = t(1);
    show(t(1));

    function show(time)
        if ~isgraphics(fig)
            return
        end
        now_t = min(max(time, t(1)), t(end));
        i = sample(t, now_t);
        q = quat_mul(quat(i, :), M.AircraftRot);
        B = [rotation(q / norm(q)), [0; 0; 0]; 0 0 0 1];  % a unit quaternion: a pure rotation
        set(body, 'Matrix', B);
        if ~isempty(parts)
            values = [];
            if ~isempty(act)
                values = act.values(sample(act.t, now_t), :);
            end
            set_controls(M, B, parts, pieces, values);
        end
        if ~isempty(dot)
            k = sample(tr.t, now_t);
            set(dot, 'XData', tr.east(k), 'YData', tr.north(k));
        end
        e = eulers(quat(i, :));
        lines = {sprintf('t       %8.2f s', now_t), sprintf('roll    %8.1f deg', e(1)), ...
            sprintf('pitch   %8.1f deg', e(2)), sprintf('heading %8.1f deg', mod(e(3), 360))};
        for h = hud
            v = value_at(h.t, h.x, now_t);
            if h.mode && isnan(v)
                v = '-';  % before the first status message
            elseif h.mode
                v = char(pprz.enum(f, 'ROTORCRAFT_STATUS', 'ap_mode', v));
            end
            lines{end + 1} = sprintf(h.format, v); %#ok<AGROW>
        end
        set(info, 'String', lines);
        set(slider, 'Value', now_t);
        drawnow;
    end

    function run(button)
        set(button, 'String', 'Pause');
        factor = [0.25 0.5 1 2 5 10];
        if now_t >= t(end)
            now_t = t(1);
        end
        start = now_t;
        clock = tic;
        while isgraphics(fig) && get(button, 'Value')
            show(start + toc(clock) * factor(get(speed, 'Value')));
            if now_t >= t(end)
                break
            end
            pause(0.03);
        end
        if isgraphics(fig)
            set(button, 'Value', 0, 'String', 'Play');
        end
    end

    function key(evt)
        step = 0.1;
        if any(strcmp(evt.Modifier, 'shift'))
            step = 1;
        end
        if strcmp(evt.Key, 'rightarrow')
            show(now_t + step);
        elseif strcmp(evt.Key, 'leftarrow')
            show(now_t - step);
        elseif strcmp(evt.Key, 'space')
            set(play, 'Value', ~get(play, 'Value'));
            if get(play, 'Value')
                run(play);
            end
        end
    end
end

function [M, name] = load_model(f, model)
    folder = fullfile(fileparts(mfilename('fullpath')), '3d_animation', '3d_models');
    if isempty(model)  % from the aircraft name
        known = {'nederdrone', 'Nederdrone5'; 'rotwing', 'RotatingWingV3'; 'rotating', 'RotatingWingV3'; ...
            'cyclone', 'Cyclone2'};
        model = 'generic';
        name = '';
        if isfield(f, 'aircraft') && isfield(f.aircraft, 'name')
            name = lower(char(f.aircraft.name));
        end
        % The logged name is often empty or generic; the airframe file usually names the aircraft too.
        if isfield(f, 'aircraft') && isfield(f.aircraft, 'config') && isfield(f.aircraft.config, 'xml') && ...
                isfield(f.aircraft.config.xml, 'airframe') && ischar(f.aircraft.config.xml.airframe)
            xml = f.aircraft.config.xml.airframe;
            name = [name ' ' lower(xml(1:min(end, 4000)))];
        end
        for k = 1:size(known, 1)
            if ~isempty(strfind(name, known{k, 1}))
                model = known{k, 2};
                break
            end
        end
    end
    if strcmpi(model, 'generic')
        M = generic_quad();
        name = 'generic quadrotor';
        return
    end
    file = model;
    if ~exist(file, 'file')
        file = fullfile(folder, [model '.mat']);
    end
    M = load(file);
    M = M.Model3D;
    [~, name] = fileparts(file);
    % The stored models (made for 3d_animation) have their tail and back wing at +x and their left parts at -y
    % after AircraftRot: x back, y right, z up. Turn them 180 deg about y into the paparazzi body frame
    % (x forward, y right, z down) so the logged attitude applies directly.
    M.AircraftRot = quat_mul([0 0 1 0], M.AircraftRot);
end

function M = generic_quad()
    % A plain X quadrotor [mm]: two arms, a body and four rotor discs (ACTUATORS columns 1-4).
    box = @(l, w, h) struct('vertices', [l w h] .* [-1 -1 -1; 1 -1 -1; 1 1 -1; -1 1 -1; -1 -1 1; 1 -1 1; 1 1 1; -1 1 1] / 2, ...
        'faces', [1 2 3 4; 5 6 7 8; 1 2 6 5; 2 3 7 6; 3 4 8 7; 4 1 5 8]);
    arm = box(640, 30, 20);
    a1 = arm;
    a1.vertices = arm.vertices * [cosd(45) sind(45) 0; -sind(45) cosd(45) 0; 0 0 1];
    a2 = arm;
    a2.vertices = arm.vertices * [cosd(-45) sind(-45) 0; -sind(-45) cosd(-45) 0; 0 0 1];
    nose = box(120, 40, 40);
    nose.vertices = nose.vertices + [120 0 0];  % shows the front
    M.AircraftRot = [1 0 0 0];
    M.Aircraft = struct('stl_data', {box(160, 120, 60), a1, a2, nose}, 'color', {[0.3 0.3 0.3], [0.5 0.5 0.5], ...
        [0.5 0.5 0.5], [0.85 0.2 0.1]});
    a = (0:20:340)';
    disc = struct('vertices', [0 0 -40; 120 * [cosd(a) sind(a)], -40 * ones(size(a))], ...
        'faces', [ones(numel(a), 1), (2:numel(a) + 1)', [(3:numel(a) + 1)'; 2]]);
    at = 226 * [1 1; -1 1; -1 -1; 1 -1];  % front right, back right, back left, front left
    for k = 1:4
        d = disc;
        d.vertices(:, 1:2) = d.vertices(:, 1:2) + at(k, :);
        M.Control(k) = struct('index', k, 'label', sprintf('M%d', k), 'color', [0.2 0.2 0.2], 'rot_point', [0 0 0], ...
            'rot_vect', [0 0 0], 'max_deflection', [0 0 0 1000 9600], 'stl_data', d, 'parent_id', 0);
    end
end

function data = controls_data(M)
    data = {};
    if isfield(M, 'Control') && ~isempty(M.Control)
        data = reshape({M.Control.stl_data}, 1, []);
    end
end

function [parts, pieces] = add_controls(M, ax)
    % One transform per control, directly in the axes: set_controls composes body, parents and deflection.
    parts = [];
    pieces = [];
    if ~isfield(M, 'Control') || isempty(M.Control)
        return
    end
    for j = 1:numel(M.Control)
        parts(j) = hgtransform('Parent', ax); %#ok<AGROW>
        pieces(j) = patch(M.Control(j).stl_data, 'Parent', parts(j), 'FaceColor', M.Control(j).color, ...
            'EdgeColor', 'none', 'FaceLighting', 'gouraud', 'AmbientStrength', 0.3, 'FaceAlpha', 0.85); %#ok<AGROW>
    end
end

function set_controls(M, B, parts, pieces, values)
    % max_deflection: [deflection at min, deflection at max (deg), actuator min, neutral, max]. A control moves
    % with its parent control (e.g. a motor on a tilting wing).
    n = numel(M.Control);
    local = repmat({eye(4)}, 1, n);
    for j = 1:n
        c = M.Control(j);
        if c.index > numel(values) || isnan(values(c.index))
            continue
        end
        v = values(c.index);
        lim = c.max_deflection;
        u = 0;
        if v > lim(4) && lim(5) ~= lim(4)
            u = (v - lim(4)) / (lim(5) - lim(4));
        elseif v < lim(4) && lim(4) ~= lim(3)
            u = (v - lim(4)) / (lim(4) - lim(3));
        end
        u = max(-1, min(1, u));
        if any(c.rot_vect)
            angle = u * (lim(2) - lim(1)) / 2;
            p = c.rot_point(:);
            local{j} = [eye(3), p; 0 0 0 1] * [axis_rotation(c.rot_vect, angle), [0; 0; 0]; 0 0 0 1] * ...
                [eye(3), -p; 0 0 0 1];
        end
        set(pieces(j), 'FaceColor', [0.15 + 0.8 * max(-u, 0), 0.15 + 0.7 * max(u, 0), 0.2]);  % red below, green above neutral
    end
    for j = 1:n
        T = local{j};
        k = M.Control(j).parent_id;
        while k > 0
            T = local{k} * T;
            k = M.Control(k).parent_id;
        end
        set(parts(j), 'Matrix', B * T);
    end
end

function [t, q] = attitude(d)
    % Body attitude quaternions [qi qx qy qz] (paparazzi: from NED to body) over time.
    if pprz.has(d, 'AHRS_REF_QUAT') && isfield(d.AHRS_REF_QUAT, 'body_qi')
        m = d.AHRS_REF_QUAT;
        q = double([m.body_qi, m.body_qx, m.body_qy, m.body_qz]);
        q = q ./ sqrt(sum(q .^ 2, 2));
    else
        msg = '';
        for name = {'ROTORCRAFT_FP', 'ATTITUDE'}
            if pprz.has(d, name{1}) && all(isfield(d.(name{1}), {'phi', 'theta', 'psi'}))
                msg = name{1};
                break
            end
        end
        if isempty(msg)
            t = [];
            q = [];
            return
        end
        m = d.(msg);
        q = quat_of_eulers(double(m.phi), double(m.theta), double(m.psi));  % deg (exported converted)
    end
    t = m.t;
    good = all(isfinite(q), 2);
    t = t(good);
    q = q(good, :);
end

function q = quat_of_eulers(phi, theta, psi)
    % ZYX Euler angles [deg] to quaternions.
    [c1, s1] = deal(cosd(psi / 2), sind(psi / 2));
    [c2, s2] = deal(cosd(theta / 2), sind(theta / 2));
    [c3, s3] = deal(cosd(phi / 2), sind(phi / 2));
    q = [c1 .* c2 .* c3 + s1 .* s2 .* s3, c1 .* c2 .* s3 - s1 .* s2 .* c3, ...
        c1 .* s2 .* c3 + s1 .* c2 .* s3, s1 .* c2 .* c3 - c1 .* s2 .* s3];
end

function e = eulers(q)
    % ZYX Euler angles [deg] of one quaternion: roll, pitch, heading.
    [w, x, y, z] = deal(q(1), q(2), q(3), q(4));
    e = [atan2d(2 * (w * x + y * z), 1 - 2 * (x * x + y * y)), asind(max(-1, min(1, 2 * (w * y - z * x)))), ...
        atan2d(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))];
end

function q = quat_mul(a, b)
    q = [a(1) * b(1) - a(2) * b(2) - a(3) * b(3) - a(4) * b(4), ...
        a(1) * b(2) + a(2) * b(1) + a(3) * b(4) - a(4) * b(3), ...
        a(1) * b(3) - a(2) * b(4) + a(3) * b(1) + a(4) * b(2), ...
        a(1) * b(4) + a(2) * b(3) - a(3) * b(2) + a(4) * b(1)];
end

function R = rotation(q)
    % The rotation that takes body coordinates to NED coordinates for q from NED to body.
    [w, x, y, z] = deal(q(1), q(2), q(3), q(4));
    R = [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y);
         2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x);
         2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)];
end

function R = axis_rotation(v, deg)
    v = v(:) / norm(v);
    K = [0 -v(3) v(2); v(3) 0 -v(1); -v(2) v(1) 0];
    R = eye(3) + sind(deg) * K + (1 - cosd(deg)) * (K * K);
end

function i = sample(t, time)
    % The last sample at or before time (the first one before the data starts).
    i = find(t <= time, 1, 'last');
    if isempty(i)
        i = 1;
    end
end

function draw_frame(ax, R)
    % North (x) and east (y) axes and a ground ring around the model.
    a = linspace(0, 2 * pi, 73);
    plot3(ax, R * cos(a), R * sin(a), zeros(size(a)), 'Color', [0.75 0.75 0.75]);
    plot3(ax, [0 R], [0 0], [0 0], 'Color', [0.8 0.2 0.2], 'LineWidth', 1.5);
    plot3(ax, [0 0], [0 R], [0 0], 'Color', [0.2 0.6 0.2], 'LineWidth', 1.5);
    text(1.1 * R, 0, 0, 'N', 'Parent', ax, 'Color', [0.8 0.2 0.2], 'FontWeight', 'bold');
    text(0, 1.1 * R, 0, 'E', 'Parent', ax, 'Color', [0.2 0.6 0.2], 'FontWeight', 'bold');
end

function hud = hud_signals(d)
    % Extra lines of the info box: a format and the samples (t, x) to show at the replay time.
    hud = struct('format', {}, 't', {}, 'x', {}, 'mode', {});
    sources = {'ROTORCRAFT_FP', 'up', 'height  %8.1f m'; 'AIR_DATA', 'airspeed', 'airspeed%8.1f m/s'; ...
        'ENERGY', 'voltage', 'battery %8.2f V'; 'ROTORCRAFT_STATUS', 'vsupply', 'battery %8.2f V'; ...
        'ROTORCRAFT_STATUS', 'ap_mode', 'mode    %s'};
    for k = 1:size(sources, 1)
        [msg, field, format] = sources{k, :};
        if ~pprz.has(d, msg) || ~isfield(d.(msg), field) || any(strcmp({hud.format}, format))
            continue  % one battery line: ENERGY when logged
        end
        hud(end + 1) = struct('format', format, 't', d.(msg).t, 'x', double(d.(msg).(field)), ...
            'mode', strcmp(field, 'ap_mode')); %#ok<AGROW>
    end
end

function v = value_at(t, x, time)
    i = find(t <= time, 1, 'last');
    v = NaN;
    if ~isempty(i)
        v = double(x(i));
    end
end
