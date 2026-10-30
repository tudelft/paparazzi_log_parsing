function plot_esc(ac_data, idx)
%PLOT_ESC ESC telemetry per motor: rpm, current, motor voltage and temperature. Motors that never report
%   rpm or current are left out; plot_esc(f.data, ids) shows only these motor ids.
    m = ac_data.ESC;
    ids = unique(m.motor_id);
    if nargin > 1
        ids = intersect(ids, idx);
    end
    alive = arrayfun(@(i) any(m.rpm(m.motor_id == i) ~= 0 | m.amps(m.motor_id == i) ~= 0), ids);
    ids = ids(alive);
    panels = {'rpm', 'Speed [rpm]'; 'amps', 'Current [A]'; 'motor_volts', 'Motor voltage [V]'; ...
        'temperature', 'Temperature [°C]'; 'temperature_dev', 'Device temperature [°C]'};
    used = cellfun(@(fld) isfield(m, fld) && any(m.(fld)(ismember(m.motor_id, ids)) ~= 0), panels(:, 1));
    panels = panels(used, :);
    axs = pprz.tiles(size(panels, 1), 'ESC');
    for p = 1:size(panels, 1)
        for i = reshape(ids, 1, [])
            rows = m.motor_id == i;
            plot(axs(p), m.t(rows), double(m.(panels{p, 1})(rows)), 'DisplayName', sprintf('Motor %d', i));
        end
        ylabel(axs(p), panels{p, 2});
    end
    legend(axs(1), 'Location', 'eastoutside');
end
