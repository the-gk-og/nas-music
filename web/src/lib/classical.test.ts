import { describe, expect, it } from 'vitest';
import { artistLine, groupByWork, hasWorks, movementTitle, toRoman } from './classical';
import { buildTree, folderChoices, folderPath } from './playlistFolders';
import type { Playlist, PlaylistFolder, Track } from './types';

const t = (id: string, extra: Partial<Track> = {}): Track =>
  ({ id, title: `T${id}`, artistId: 'a', artist: 'Orchestra', albumId: 'al', album: 'Al', durationS: 60, starred: false, ...extra }) as Track;

describe('classical mode', () => {
  const tracks = [
    t('1', { work: 'Sym 5', workId: 'w5', composer: 'Beethoven', movement: 'Allegro', movementNo: 1 }),
    t('2', { work: 'Sym 5', workId: 'w5', composer: 'Beethoven', movement: 'Andante', movementNo: 2 }),
    t('3'),
    t('4', { work: 'Sym 6', workId: 'w6', composer: 'Beethoven', movement: 'Erwachen' }),
  ];

  it('groups consecutive movements of a work', () => {
    const g = groupByWork(tracks);
    expect(g.map((x) => [x.workId, x.tracks.length, x.start])).toEqual([['w5', 2, 0], [null, 1, 2], ['w6', 1, 3]]);
    expect(hasWorks(tracks)).toBe(true);
    expect(hasWorks([t('9')])).toBe(false);
  });

  it('shows movement names and the composer first', () => {
    expect(movementTitle(tracks[1])).toBe('II. Andante');
    expect(movementTitle(tracks[3])).toBe('Erwachen');
    expect(movementTitle(tracks[2])).toBe('T3');
    expect(artistLine(tracks[0], true)).toBe('Beethoven · Orchestra');
    expect(artistLine(tracks[0], false)).toBe('Orchestra');
    expect([1, 4, 9, 14, 40].map(toRoman)).toEqual(['I', 'IV', 'IX', 'XIV', 'XL']);
  });
});

describe('playlist folders', () => {
  const folders: PlaylistFolder[] = [
    { id: 'a', name: 'A', parentId: null, playlistCount: 1 },
    { id: 'b', name: 'B', parentId: 'a', playlistCount: 1 },
    { id: 'c', name: 'C', parentId: null, playlistCount: 0 },
  ];
  const pls = [
    { id: 'p1', name: 'P1', folderId: 'a' },
    { id: 'p2', name: 'P2', folderId: 'b' },
    { id: 'p3', name: 'P3', folderId: null },
    { id: 'p4', name: 'P4', folderId: 'gone' },
  ] as Playlist[];

  it('builds the tree', () => {
    const { roots, loose } = buildTree(folders, pls);
    expect(roots.map((n) => n.folder.id)).toEqual(['a', 'c']);
    expect(roots[0].children[0].depth).toBe(1);
    expect(roots[0].children[0].playlists.map((p) => p.id)).toEqual(['p2']);
    expect(loose.map((p) => p.id)).toEqual(['p3', 'p4']);
  });

  it('paths and move choices', () => {
    expect(folderPath(folders, 'b').map((f) => f.id)).toEqual(['a', 'b']);
    expect(folderPath(folders, null)).toEqual([]);
    expect(folderChoices(folders).map((c) => [c.value, c.depth])).toEqual([['', 0], ['a', 1], ['b', 2], ['c', 1]]);
    // a folder can't move into itself or its subfolders
    expect(folderChoices(folders, 'a').map((c) => c.value)).toEqual(['', 'c']);
  });
});
