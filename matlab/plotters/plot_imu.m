function plot_imu(ac_data, f)
%PLOT_IMU Accelerometer, gyro and magnetometer per sensor id, from the raw IMU messages or the _SCALED ones.
    sensors = {'IMU_ACCEL', {'ax', 'ay', 'az'}, 'Accelerometer'; 'IMU_GYRO', {'gp', 'gq', 'gr'}, 'Gyro'; ...
        'IMU_MAG', {'mx', 'my', 'mz'}, 'Magnetometer'};
    for s = 1:size(sensors, 1)
        if ~pprz.has(ac_data, sensors{s, 1}) && pprz.has(ac_data, [sensors{s, 1} '_SCALED'])
            sensors{s, 1} = [sensors{s, 1} '_SCALED'];
        end
    end
    sensors = sensors(cellfun(@(m) pprz.has(ac_data, m), sensors(:, 1)), :);
    if isempty(sensors)
        return
    end
    axs = pprz.tiles(size(sensors, 1), 'IMU');
    for s = 1:size(sensors, 1)
        m = ac_data.(sensors{s, 1});
        ids = 0;
        if isfield(m, 'id')
            ids = unique(m.id);
        end
        for id = reshape(ids, 1, [])
            rows = true(size(m.t));
            if isfield(m, 'id')
                rows = m.id == id;
            end
            for c = 1:3
                name = sensors{s, 2}{c};
                if numel(ids) > 1
                    name = sprintf('%s (id %d)', name, id);
                end
                plot(axs(s), m.t(rows), double(m.(sensors{s, 2}{c})(rows)), 'DisplayName', name);
            end
        end
        ylabel(axs(s), sprintf('%s [%s]', sensors{s, 3}, unit(f, sensors{s, 1}, sensors{s, 2}{1})), ...
            'Interpreter', 'none');
        legend(axs(s), 'Location', 'eastoutside', 'Interpreter', 'none');
    end
end

function u = unit(f, msg, field)
    % The unit as exported (gyro in deg/s); '-' when unknown.
    u = '-';
    try
        u = char(f.messages.(msg).units.(field));
    catch
    end
    if isempty(u)
        u = '-';
    end
end
