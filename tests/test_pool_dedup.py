"""Tests for not counting a shared storage pool's free space N times.

The reported defect: a TrueNAS host showed 24.5 TiB of storage against a real
~6.5 TiB. Its SNMP agent listed 40 filesystems, and a pooled filesystem quotes
the POOL's free space inside EVERY dataset's size, so summing sizes multiplies
that one number by the dataset count. Twelve mounts quoting an identical
1061.33 GiB was the giveaway.

The numbers below are the real ones from that host. `/mnt/POOL1/Veeam` at
6508.48 total and 5447.15 used leaves 1061.33 free, and every sibling dataset
reports its own used plus that same 1061.33.

The two tests that matter most are not the TrueNAS one — they are the two that
say what must NOT change. A host with no pooling has to come out byte-identical
to the naive sum, and several full filesystems must not collapse into one just
because they all report zero free.
"""
from __future__ import annotations

from logic.merge import dedupe_shared_pool_totals

GIB = 1024 ** 3


def _pool(free_bytes: int, useds: list) -> list:
    """Build one pool's datasets the way a pooled filesystem reports them.

    Every dataset's size is its own used plus the pool's shared free space,
    which is the whole reason the naive sum goes wrong.
    """
    return [(u + free_bytes, u) for u in useds]


def test_a_pool_counts_its_free_space_once():
    free = int(1061.33 * GIB)
    useds = [int(5447.15 * GIB), int(1.56 * GIB), int(0.90 * GIB)] + [0] * 15
    entries = _pool(free, useds)

    naive = sum(t for t, _ in entries)
    total, used = dedupe_shared_pool_totals(entries)

    assert used == sum(useds)
    assert total == sum(useds) + free
    # 18 datasets: the naive sum carries the free space 18 times over.
    assert naive > total * 3


def test_the_reported_truenas_host_lands_near_its_real_size():
    """Both pools together, as the host actually reports them."""
    pool1 = _pool(int(1061.33 * GIB),
                  [int(5447.15 * GIB), int(1.56 * GIB), int(0.90 * GIB),
                   int(0.37 * GIB), int(0.37 * GIB), int(0.04 * GIB)] + [0] * 12)
    boot = _pool(int(30.26 * GIB),
                 [int(2.56 * GIB), int(0.19 * GIB), int(0.17 * GIB),
                  int(0.05 * GIB), int(0.03 * GIB)] + [0] * 12)

    total, _ = dedupe_shared_pool_totals(pool1 + boot)
    tib = total / 1024 ** 4

    # Real storage is about 6.5 TiB; the defect reported 24.5 TiB.
    assert 6.0 < tib < 7.0, f"expected roughly 6.5 TiB, got {tib:.2f}"


def test_a_host_with_no_pooling_is_untouched():
    """The property that keeps this safe for every other host.

    Distinct free-space figures mean every group holds one filesystem, so the
    result has to equal the naive sum exactly — not approximately.
    """
    entries = [(500 * GIB, 100 * GIB), (250 * GIB, 30 * GIB), (80 * GIB, 79 * GIB)]
    total, used = dedupe_shared_pool_totals(entries)
    assert total == sum(t for t, _ in entries)
    assert used == sum(u for _, u in entries)


def test_full_filesystems_are_not_merged():
    """Several disks at zero free are separate disks, not one pool.

    Zero is the one free-space value that legitimately repeats across
    unrelated filesystems, so it must never be used to group them.
    """
    entries = [(100 * GIB, 100 * GIB), (200 * GIB, 200 * GIB)]
    total, used = dedupe_shared_pool_totals(entries)
    assert total == 300 * GIB
    assert used == 300 * GIB


def test_two_separate_pools_each_keep_their_own_free_space():
    """Grouping is per free-space figure, so distinct pools stay distinct."""
    a = _pool(10 * GIB, [5 * GIB, 1 * GIB])
    b = _pool(40 * GIB, [7 * GIB, 2 * GIB])
    total, _ = dedupe_shared_pool_totals(a + b)
    assert total == (6 * GIB + 10 * GIB) + (9 * GIB + 40 * GIB)


def test_junk_rows_are_skipped_without_raising():
    """SNMP walks return partial rows; a bad one must not take the total with
    it."""
    entries = [(100 * GIB, 10 * GIB), (None, 5), ("x", "y"), (0, 0), (-5, 2)]
    total, used = dedupe_shared_pool_totals(entries)
    assert total == 100 * GIB
    assert used == 10 * GIB


def test_used_is_clamped_to_the_size():
    """A device reporting used > size must not produce a negative free."""
    total, used = dedupe_shared_pool_totals([(100 * GIB, 500 * GIB)])
    assert total == 100 * GIB
    assert used == 100 * GIB


def test_both_snmp_paths_route_through_the_dedup():
    """hrStorage and UCD both summed sizes naively; neither may regress."""
    import inspect
    from logic import snmp
    src = inspect.getsource(snmp)
    assert src.count("_dedupe_shared_pool_totals(") >= 2, (
        "an SNMP filesystem total is summing sizes without the pool dedup")
    assert "disk_total += total_bytes" not in src, (
        "the hrStorage naive sum is back")
    assert "disk_total_sum += total_b" not in src, (
        "the UCD naive sum is back")

def test_beszel_extra_filesystems_is_deduped_too():
    """The same roll-up exists on the Beszel side, reachable the same way."""
    import inspect
    from logic import beszel
    src = inspect.getsource(beszel)
    assert "_dedupe_shared_pool_totals(" in src, (
        "the EXTRA_FILESYSTEMS roll-up is summing sizes naively again")
    assert "efs_total_gib += " not in src, "the naive EFS sum is back"


def test_a_single_extra_filesystem_is_unchanged():
    """The shape every configured agent on this fleet actually has."""
    total, used = dedupe_shared_pool_totals([(48 * GIB, 35 * GIB)])
    assert total == 48 * GIB and used == 35 * GIB

def test_the_post_merge_mounts_override_is_pool_aware():
    """The path that actually decides the displayed number.

    A post-merge step re-sums mounts[] and overrides host_disk_total when
    that sum exceeds 1.5x it. Summed naively, a pooled host ALWAYS trips
    that threshold -- inflation by dataset count is the bug itself -- so the
    override fired hardest exactly where it was most wrong, replacing
    node_exporter's correctly-deduped 0.86 TiB with 8.60 TiB.

    Deduping the providers alone would have been silently undone here, so
    this assertion is what keeps the whole fix connected to the UI.
    """
    import inspect
    from main_pkg import hosts_merge_routes
    src = inspect.getsource(hosts_merge_routes)
    assert "_dedupe_shared_pool_totals(_m_entries)" in src, (
        "the mounts-aggregate override is summing sizes naively again -- any"
        " provider-side dedup is now defeated downstream")
    assert 'm_total += float(_m.get("d") or 0)' not in src, (
        "the naive mounts sum is back")
