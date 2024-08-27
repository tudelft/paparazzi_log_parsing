function plot_rotwing_state(ac_data, vert)
    if ~isfield(ac_data, 'ROTATING_WING_STATE')
        return
    end
    
    % Plot the state
    ax1 = subplot(3,1,1);
    skew_angle_valid = bitget(ac_data.ROTATING_WING_STATE.status, 1);
    hover_motors_enabled = bitget(ac_data.ROTATING_WING_STATE.status, 2) .*2;
    hover_motors_idle = bitget(ac_data.ROTATING_WING_STATE.status, 3) .*2;
    hover_motors_running = bitget(ac_data.ROTATING_WING_STATE.status, 4) .*2;
    pusher_motor_running = bitget(ac_data.ROTATING_WING_STATE.status, 5) .*3;
    skew_forced = bitget(ac_data.ROTATING_WING_STATE.status, 6);

    plot(ac_data.ROTATING_WING_STATE.timestamp, [ac_data.ROTATING_WING_STATE.state, ac_data.ROTATING_WING_STATE.nav_state], '-')
    hold on
    plot(ac_data.ROTATING_WING_STATE.timestamp, [skew_angle_valid, hover_motors_enabled, hover_motors_idle, hover_motors_running, pusher_motor_running, skew_forced], "-.");
    if exist('vert','var')
        hold on;
        plot([vert'; vert'], repmat(ylim',1,size(vert,1)), '--r');
    end
    title('State');
    xlabel('Time [s]');
    legend('State', 'NAV State', 'Skew angle valid', 'Hover motors enabled', 'Hover motors idle', 'Hover motors running', 'Pusher motor running', 'Skew forced');

    ax2 = subplot(3,1,2);
    plot(ac_data.ROTATING_WING_STATE.timestamp, [ac_data.ROTATING_WING_STATE.meas_skew_angle, ac_data.ROTATING_WING_STATE.sp_skew_angle]);
    if exist('vert','var')
        hold on;
        plot([vert'; vert'], repmat(ylim',1,size(vert,1)), '--r');
    end
    title('Skew');
    xlabel('Time [s]');
    legend('Measured [deg]', 'Setpoint [deg]');

    ax3 = subplot(3,1,3);
    resamp_airspeed = resample(timeseries(ac_data.AIR_DATA.airspeed, ac_data.AIR_DATA.timestamp), ac_data.ROTATING_WING_STATE.timestamp);
    plot(ac_data.ROTATING_WING_STATE.timestamp, [resamp_airspeed.Data, ac_data.ROTATING_WING_STATE.nav_airspeed, ac_data.ROTATING_WING_STATE.min_airspeed,  ac_data.ROTATING_WING_STATE.max_airspeed]);
    if exist('vert','var')
        hold on;
        plot([vert'; vert'], repmat(ylim',1,size(vert,1)), '--r');
    end
    title('Airspeed');
    xlabel('Time [s]');
    legend('Meas airspeed [m/s]', 'NAV Airspeed [m/s]', 'Min Airspeed [m/s]', 'Max Airspeed [m/s]');

    sgtitle('Rotwing state')
    linkaxes([ax1,ax2,ax3],'x')
end