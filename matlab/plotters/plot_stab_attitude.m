function plot_stab_attitude(ac_data, order)
    if ~isfield(ac_data, 'STAB_ATTITUDE')
        return
    end

    ax1 = subplot(3,1,1);
    hold on
    plot(ac_data.STAB_ATTITUDE.timestamp, ac_data.STAB_ATTITUDE.angular_accel_p, 'color', '#0072BD');
    plot(ac_data.STAB_ATTITUDE.timestamp, ac_data.STAB_ATTITUDE.angular_accel_ref_p, '--', 'color', '#0072BD');
    plot(ac_data.STAB_ATTITUDE.timestamp, ac_data.STAB_ATTITUDE.angular_accel_q, 'color', '#D95319');
    plot(ac_data.STAB_ATTITUDE.timestamp, ac_data.STAB_ATTITUDE.angular_accel_ref_q, '--', 'color', '#D95319');
    plot(ac_data.STAB_ATTITUDE.timestamp, ac_data.STAB_ATTITUDE.angular_accel_r, 'color', '#EDB120');
    plot(ac_data.STAB_ATTITUDE.timestamp, ac_data.STAB_ATTITUDE.angular_accel_ref_r, '--', 'color', '#EDB120');
    title('angular acceleration'); xlabel('time(s)'); ylabel('(rad/s^2)'); legend('pdot','pdotref','qdot','qdotref','rdot','rdotref')

    ax2 = subplot(3,1,2);
    hold on
    plot(ac_data.STAB_ATTITUDE.timestamp, ac_data.STAB_ATTITUDE.angular_rate_p, 'color', '#0072BD');
    plot(ac_data.STAB_ATTITUDE.timestamp, ac_data.STAB_ATTITUDE.angular_rate_ref_p, '--', 'color', '#0072BD');
    plot(ac_data.STAB_ATTITUDE.timestamp, ac_data.STAB_ATTITUDE.angular_rate_q, 'color', '#D95319');
    plot(ac_data.STAB_ATTITUDE.timestamp, ac_data.STAB_ATTITUDE.angular_rate_ref_q, '--', 'color', '#D95319');
    plot(ac_data.STAB_ATTITUDE.timestamp, ac_data.STAB_ATTITUDE.angular_rate_r, 'color', '#EDB120');
    plot(ac_data.STAB_ATTITUDE.timestamp, ac_data.STAB_ATTITUDE.angular_rate_ref_r, '--', 'color', '#EDB120');
    title('angular rates'); xlabel('time(s)'); ylabel('(rad/s)'); legend('p','pref','q','qref','r','rref')

    ax3 = subplot(3,1,3);
    hold on
    plot(ac_data.STAB_ATTITUDE.timestamp, ac_data.STAB_ATTITUDE.phi, 'color', '#0072BD');
    plot(ac_data.STAB_ATTITUDE.timestamp, ac_data.STAB_ATTITUDE.phi_ref, '--', 'color', '#0072BD');
    plot(ac_data.STAB_ATTITUDE.timestamp, ac_data.STAB_ATTITUDE.theta, 'color', '#D95319');
    plot(ac_data.STAB_ATTITUDE.timestamp, ac_data.STAB_ATTITUDE.theta_ref, '--', 'color', '#D95319');
    plot(ac_data.STAB_ATTITUDE.timestamp, ac_data.STAB_ATTITUDE.psi, 'color', '#EDB120');
    plot(ac_data.STAB_ATTITUDE.timestamp, ac_data.STAB_ATTITUDE.psi_ref, '--', 'color', '#EDB120');
    title('Euler angles'); xlabel('time(s)'); ylabel('(rad)'); legend('phi','phiref','theta','thetaref','psi','psiref')

    sgtitle(['Stab attitude'])
    linkaxes([ax1,ax2,ax3],'x')
end