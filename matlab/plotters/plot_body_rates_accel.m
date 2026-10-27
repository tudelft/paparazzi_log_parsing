function plot_body_rates_accel(ac_data)
    if ~isfield(ac_data, 'BODY_RATES_ACCEL')
        return
    end

    % Plot the body rates
    ax1 = subplot(2,1,1);
    plot(ac_data.BODY_RATES_ACCEL.t, [ac_data.BODY_RATES_ACCEL.ax, ac_data.BODY_RATES_ACCEL.ay, ac_data.BODY_RATES_ACCEL.az]);
    title('Body acceleration');
    xlabel('Time [s]');
    legend('x [m/s^2]', 'y [m/s^2]', 'z [m/s^2]');

    ax2 = subplot(2,1,2);
    plot(ac_data.BODY_RATES_ACCEL.t, [ac_data.BODY_RATES_ACCEL.p, ac_data.BODY_RATES_ACCEL.q, ac_data.BODY_RATES_ACCEL.r]/pi*180);
    title('Body rates');
    xlabel('Time [s]');
    legend('p [deg/s]', 'q [deg/s]', 'r [deg/s]');

    sgtitle('Body rates accel')
    linkaxes([ax1,ax2],'x')
end