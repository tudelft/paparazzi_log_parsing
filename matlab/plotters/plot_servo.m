function plot_servo(ac_data, idx)
%PLOT_SERVO Servo telemetry per servo (SERVO_TELEMETRY, pprzlink 2026-11): position, speed, load, current,
%   voltage and temperature, see pprz.servo. Panels without data are left out; plot_servo(f.data, ids) shows only
%   these actuator indexes. Older logs have no servo telemetry.
    s = pprz.servo(ac_data);
    ids = unique(s.id);
    if nargin > 1
        ids = intersect(ids, idx);
    end
    s.position = rad2deg(s.position);
    panels = {'position', 'Position [deg]'; 'speed', 'Speed [rad/s]'; 'force', 'Load [N or Nm]'; ...
        'current', 'Current [A]'; 'voltage', 'Voltage [V]'; 'temperature', 'Temperature [°C]'; ...
        'errors', 'Errors'};
    rows = ismember(s.id, ids);
    used = cellfun(@(fld) any(~isnan(s.(fld)(rows)) & s.(fld)(rows) ~= 0), panels(:, 1));
    panels = panels(used, :);
    if isempty(panels)
        panels = {'position', 'Position [deg]'};  % nothing reported: one empty panel instead of none
    end
    axs = pprz.tiles(size(panels, 1), 'Servos (SERVO_TELEMETRY)');
    for p = 1:size(panels, 1)
        for i = reshape(ids, 1, [])
            in = s.id == i;
            plot(axs(p), s.t(in), s.(panels{p, 1})(in), 'DisplayName', sprintf('Servo %d', i));
        end
        ylabel(axs(p), panels{p, 2});
    end
    if ~isempty(ids)
        legend(axs(1), 'Location', 'eastoutside');
    end
end
