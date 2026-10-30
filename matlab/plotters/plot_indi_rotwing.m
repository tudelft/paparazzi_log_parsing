function plot_indi_rotwing(ac_data)
%PLOT_INDI_ROTWING INDI commands of the rotating wing (STAB_ATTITUDE.u: front, right, back, left, rudder,
%   elevator, aileron, flaps, pusher) and the specific thrust of the four hover motors.
    m = ac_data.STAB_ATTITUDE;
    u = double(m.u);
    if size(u, 2) < 9
        error('plot_indi_rotwing:u', 'STAB_ATTITUDE.u has %d values; the rotating wing sends 9.', size(u, 2));
    end
    axs = pprz.tiles(2, 'INDI rotating wing');
    pprz.series(axs(1), m.t, u(:, 1:9), {'Front', 'Right', 'Back', 'Left', 'Rudder', 'Elevator', 'Aileron', ...
        'Flaps', 'Pusher'});
    ylabel(axs(1), 'INDI u');
    plot(axs(2), m.t, sum(u(:, 1:4), 2) * -0.000575, 'DisplayName', 'Specific thrust');
    ylabel(axs(2), 'Specific thrust [m/s²]');
end
