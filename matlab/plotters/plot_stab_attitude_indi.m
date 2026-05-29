function plot_STAB_ATTITUDE_INDI(ac_data)
    if ~isfield(ac_data, 'STAB_ATTITUDE_INDI')
        return
    end

    figure
    hold on
    plot(ac_data.STAB_ATTITUDE_INDI.timestamp, ac_data.STAB_ATTITUDE_INDI.angular_accel_p, 'color', '#0072BD');
    plot(ac_data.STAB_ATTITUDE_INDI.timestamp, ac_data.STAB_ATTITUDE_INDI.angular_accel_ref_p, '--', 'color', '#0072BD');
    plot(ac_data.STAB_ATTITUDE_INDI.timestamp, ac_data.STAB_ATTITUDE_INDI.angular_accel_q, 'color', '#D95319');
    plot(ac_data.STAB_ATTITUDE_INDI.timestamp, ac_data.STAB_ATTITUDE_INDI.angular_accel_ref_q, '--', 'color', '#D95319');
    plot(ac_data.STAB_ATTITUDE_INDI.timestamp, ac_data.STAB_ATTITUDE_INDI.angular_accel_r, 'color', '#EDB120');
    plot(ac_data.STAB_ATTITUDE_INDI.timestamp, ac_data.STAB_ATTITUDE_INDI.angular_accel_ref_r, '--', 'color', '#EDB120');
    title('angular acceleration'); xlabel('time(s)'); ylabel('(rad/s^2)'); legend('pdot','pdotref','qdot','qdotref','rdot','rdotref')

end