function plot_vibration(f)
%PLOT_VIBRATION Accelerometer and gyro spectrograms over time with the rotor frequencies, and frequency
%   against rotor speed (Campbell). Rotor speed is the mean ESC rpm when logged (1× and 2× rotor
%   frequency are drawn; rotors that are stopped do not count), else the mean actuator command. Uses
%   IMU_ACCEL/IMU_GYRO, or the _SCALED messages when only those were logged.
    d = f.data;
    [ts, speed, label, rpm] = rotor_speed(d);
    sensors = {'IMU_ACCEL', {'ax', 'ay', 'az'}, 'Accelerometer'; 'IMU_GYRO', {'gp', 'gq', 'gr'}, 'Gyro'};
    for s = 1:2
        if ~pprz.has(d, sensors{s, 1}) && pprz.has(d, [sensors{s, 1} '_SCALED'])
            sensors{s, 1} = [sensors{s, 1} '_SCALED'];
        end
    end
    sensors = sensors(cellfun(@(m) pprz.has(d, m), sensors(:, 1)), :);
    rows = size(sensors, 1) + ~isempty(ts);
    time_axes = gobjects(0);
    for s = 1:size(sensors, 1)
        m = d.(sensors{s, 1});
        sel = true(size(m.t));
        if isfield(m, 'id')
            ids = unique(m.id);
            sel = m.id == ids(1);
        end
        x = zeros(nnz(sel), 3);
        for k = 1:3
            x(:, k) = m.(sensors{s, 2}{k})(sel);
        end
        [P, freq, t] = pprz.spectrogram(m.t(sel), x);
        db = 10 * log10(P + eps);
        ax = subplot(rows, 3, (s - 1) * 3 + [1 2]);
        imagesc(ax, t, freq, db);
        set(ax, 'YDir', 'normal');
        hold(ax, 'on');
        colormap(ax, parula(256));
        clim_(ax, db);
        ylabel(ax, 'Frequency [Hz]');
        title(ax, sprintf('%s (all axes)', sensors{s, 3}), 'FontWeight', 'normal');
        if rpm
            hz = interp1(ts, speed / 60, t, 'linear', NaN);
            plot(ax, t, hz, 'w-', 'LineWidth', 1, 'DisplayName', '1× rotor');
            plot(ax, t, 2 * hz, 'w--', 'LineWidth', 1, 'DisplayName', '2× rotor');
        end
        xlabel(ax, 'Time [s]');
        time_axes(end + 1) = ax; %#ok<AGROW>

        ax = subplot(rows, 3, s * 3);
        campbell(ax, t, freq, P, ts, speed, label, rpm);
    end
    if ~isempty(ts)
        ax = subplot(rows, 3, (rows - 1) * 3 + [1 2]);
        plot(ax, ts, speed);
        grid(ax, 'on');
        ylabel(ax, label);
        xlabel(ax, 'Time [s]');
        time_axes(end + 1) = ax;
    end
    if numel(time_axes) > 1
        linkaxes(time_axes, 'x');
    end
end

function [t, speed, label, rpm] = rotor_speed(d)
    [t, speed, label, rpm] = deal([], [], '', false);
    if pprz.has(d, 'ESC') && any(d.ESC.rpm > 0)
        ids = unique(d.ESC.motor_id);
        first = d.ESC.motor_id == ids(1);
        t = unique(d.ESC.t(first));
        all_ = zeros(numel(t), numel(ids));
        for k = 1:numel(ids)
            rows = d.ESC.motor_id == ids(k);
            [tk, kk] = unique(d.ESC.t(rows));
            v = double(d.ESC.rpm(rows));
            all_(:, k) = interp1(tk, v(kk), t, 'linear', 'extrap');
        end
        all_ = all_(:, any(all_ > 100, 1));  % ESCs that never spin
        all_(all_ < 100) = NaN;             % stopped rotors do not count
        speed = zeros(size(t));
        for i = 1:numel(t)
            v = all_(i, ~isnan(all_(i, :)));
            if ~isempty(v)
                speed(i) = mean(v);
            end
        end
        label = 'Mean speed of running rotors [rpm]';
        rpm = true;
    elseif pprz.has(d, 'ACTUATORS')
        [u, ~, t] = pprz.actuators(d);
        [t, keep] = unique(t);
        speed = mean(u(keep, :), 2);
        label = 'Mean actuator command';
    end
end

function campbell(ax, t, freq, P, ts, speed, label, rpm)
    % Mean spectrum per rotor speed bin.
    if isempty(ts)
        semilogy(ax, freq, mean(P, 2));
        grid(ax, 'on');
        xlabel(ax, 'Frequency [Hz]');
        ylabel(ax, 'PSD');
        title(ax, 'Mean spectrum', 'FontWeight', 'normal');
        return
    end
    s = interp1(ts, speed, t, 'linear', NaN);
    ok = isfinite(s);
    edges = linspace(min(s(ok)), max(s(ok)) + eps, 31);
    centres = (edges(1:end - 1) + edges(2:end)) / 2;
    img = NaN(numel(freq), numel(centres));
    for b = 1:numel(centres)
        in = ok & s >= edges(b) & s < edges(b + 1);
        if any(in)
            img(:, b) = 10 * log10(mean(P(:, in), 2) + eps);
        end
    end
    imagesc(ax, centres, freq, img, 'AlphaData', double(~isnan(img)));
    set(ax, 'YDir', 'normal');
    hold(ax, 'on');
    colormap(ax, parula(256));
    clim_(ax, img);
    if rpm
        plot(ax, centres, centres / 60, 'w-', centres, 2 * centres / 60, 'w--');
    end
    set(ax, 'YLim', [freq(1) freq(end)]);
    xlabel(ax, label);
    ylabel(ax, 'Frequency [Hz]');
    if rpm
        title(ax, 'Against rotor speed [dB]', 'FontWeight', 'normal');
    else
        title(ax, 'Against actuator command [dB]', 'FontWeight', 'normal');
    end
end

function clim_(ax, db)
    % Colour range from the 5th to the 99.5th percentile, so a few peaks do not wash out the rest.
    v = sort(db(isfinite(db)));
    if numel(v) > 10
        set(ax, 'CLim', [v(max(1, round(0.05 * numel(v)))), v(round(0.995 * numel(v)))]);
    end
end
